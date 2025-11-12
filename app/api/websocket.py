import asyncio
import json
import base64
import traceback
import numpy as np
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.streaming.manager import MedicareAgent
from app.api.http import get_agent_manager
from app.audio.stt import transcribe_audio
from app.audio import vad
from app.streaming.pipeline import llm_producer, tts_consumer

# Import all config variables
from app.config import (
    VAD_SILENCE_TIMEOUT_MS,
    VAD_SPEECH_THRESHOLD,
    MIN_BARGEIN_SPEECH_MS,
    MIN_BARGEIN_SPEECH_CHUNKS,
    MIN_SPEECH_DURATION_MS,
    MIN_AUDIO_ENERGY,
    PREEMPHASIS_ALPHA,
    ENABLE_AUDIO_LOGGING,
    AUDIO_LOG_DIR,
    DEBUG_PRINT_AUDIO_STATS,
    DEBUG_PRINT_VAD_DECISIONS,
    AUDIO_QUEUE_CHECK_INTERVAL,
    VAD_CHUNK_BYTES,
    MS_PER_VAD_CHUNK,
    AGENT_SAMPLE_RATE
)

router = APIRouter()

# Create audio log directory if logging is enabled
if ENABLE_AUDIO_LOGGING:
    AUDIO_LOG_DIR.mkdir(exist_ok=True)

# Audio logging helper
_audio_counters = {}

def save_audio_chunk(audio_bytes: bytes, session_id: str, stage: str):
    """Save audio chunk for debugging."""
    if not ENABLE_AUDIO_LOGGING:
        return
    
    if session_id not in _audio_counters:
        _audio_counters[session_id] = {}
    if stage not in _audio_counters[session_id]:
        _audio_counters[session_id][stage] = 0
    
    counter = _audio_counters[session_id][stage]
    filename = AUDIO_LOG_DIR / f"{session_id}_{stage}_{counter:04d}.raw"
    
    try:
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        _audio_counters[session_id][stage] += 1
    except Exception as e:
        print(f"[{session_id}] Failed to save audio: {e}")

async def clear_async_queue(q: asyncio.Queue):
    """Remove all items from an asyncio Queue."""
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except asyncio.QueueEmpty:
            break

async def audio_sender_task(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    interruption_event: asyncio.Event,
    agent_is_speaking_event: asyncio.Event,
    session_id: str
):
    """
    Audio sender with IMMEDIATE interruption handling.
    Uses config: AUDIO_QUEUE_CHECK_INTERVAL, ENABLE_AUDIO_LOGGING
    """
    try:
        while True:
            # CRITICAL: Check interruption BEFORE attempting to get audio
            if interruption_event.is_set():
                print(f"[{session_id}] 🚨 INTERRUPT ACTIVE - Sending stop signal")
                
                # 1. Send interrupt to relay IMMEDIATELY
                await websocket.send_json({"type": "interrupt"})
                
                # 2. Clear all pending audio
                cleared = 0
                while not audio_queue.empty():
                    try:
                        audio_queue.get_nowait()
                        audio_queue.task_done()
                        cleared += 1
                    except asyncio.QueueEmpty:
                        break
                
                if cleared > 0:
                    print(f"[{session_id}] 🗑️  Cleared {cleared} audio chunks from queue")
                
                # 3. Reset flags AFTER clearing
                interruption_event.clear()
                agent_is_speaking_event.clear()
                
                # 4. Small delay to ensure relay processes interrupt
                await asyncio.sleep(0.05)
                continue
            
            # Non-blocking queue check with configurable timeout
            try:
                audio_chunk = await asyncio.wait_for(
                    audio_queue.get(), 
                    timeout=AUDIO_QUEUE_CHECK_INTERVAL
                )
            except asyncio.TimeoutError:
                continue
            
            if audio_chunk is None:
                print(f"[{session_id}] Audio sender: Turn complete sentinel")
                audio_queue.task_done()
                agent_is_speaking_event.clear()
                continue

            # Double-check interruption before sending
            if interruption_event.is_set():
                audio_queue.task_done()
                continue

            # Log TTS output if enabled
            save_audio_chunk(audio_chunk, session_id, "tts_output")

            chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
            await websocket.send_json({
                "type": "audio_response",
                "audio": chunk_b64,
                "format": "pcm16k",
                "sample_rate": AGENT_SAMPLE_RATE
            })
            audio_queue.task_done()
            
    except WebSocketDisconnect:
        print(f"[{session_id}] Audio sender disconnected.")
    except asyncio.CancelledError:
        print(f"[{session_id}] Audio sender cancelled.")
    except Exception as e:
        print(f"[{session_id}] Audio sender error: {e}")
        traceback.print_exc()
    finally:
        print(f"[{session_id}] Audio sender task exiting.")


async def agent_handler_task(
    session_id: str,
    transcript_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    agent_is_speaking_event: asyncio.Event,
    interruption_event: asyncio.Event
):
    """Agent LLM/TTS pipeline handler."""
    try:
        while True:
            transcript = await transcript_queue.get()
            if transcript is None:
                break

            # Clear any previous interruption state
            interruption_event.clear()
            agent_is_speaking_event.set()
            
            sentence_queue = asyncio.Queue()
            
            producer_task = asyncio.create_task(
                llm_producer(session_id, transcript, sentence_queue, interruption_event)
            )
            
            consumer_task = asyncio.create_task(
                tts_consumer(sentence_queue, audio_queue, interruption_event, output_format="pcm16k")
            )

            interruption_wait_task = asyncio.create_task(
                interruption_event.wait()
            )
            
            done, pending = await asyncio.wait(
                [producer_task, interruption_wait_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            if interruption_wait_task in done:
                print(f"[{session_id}] 🚨 INTERRUPTION - Killing pipeline")
                
                # Cancel tasks
                producer_task.cancel()
                consumer_task.cancel()
                
                # Clear queues
                await clear_async_queue(sentence_queue)
                await clear_async_queue(audio_queue)
                
                # Wait for cancellation
                await asyncio.gather(producer_task, consumer_task, return_exceptions=True)
                
                print(f"[{session_id}] ✅ Pipeline stopped, ready for new input")
            else:
                print(f"[{session_id}] LLM finished naturally, waiting for TTS.")
                interruption_wait_task.cancel()
                await consumer_task

            agent_is_speaking_event.clear()
            transcript_queue.task_done()
            
    except asyncio.CancelledError:
        print(f"[{session_id}] Agent handler cancelled.")
    except Exception as e:
        print(f"[{session_id}] Agent handler error: {e}")
        traceback.print_exc()
    finally:
        agent_is_speaking_event.clear()
        print(f"[{session_id}] Agent handler exiting.")


async def audio_receiver_task(
    websocket: WebSocket,
    session_id: str,
    transcript_queue: asyncio.Queue,
    agent_is_speaking_event: asyncio.Event,
    interruption_event: asyncio.Event
):
    """
    Receives pcm16k, validates quality, runs VAD with proper buffering.
    Uses all config variables for tunable behavior.
    """
    vad_model, vad_utils = vad.get_vad_model()
    if vad_model is None:
        print(f"[{session_id}] VAD model not loaded.")
        return

    # VAD state
    pcm16k_buffer = bytearray()
    speech_buffer_pcm = bytearray()
    is_speaking = False
    speech_chunks = 0  # Count consecutive speech chunks
    silent_chunks = 0
    
    # Calculate derived values from config
    SILENT_CHUNKS_FOR_EOS = int(VAD_SILENCE_TIMEOUT_MS / MS_PER_VAD_CHUNK)
    MIN_SPEECH_CHUNKS = int(MIN_SPEECH_DURATION_MS / MS_PER_VAD_CHUNK)
    
    # Barge-in protection
    consecutive_speech_during_agent = 0
    
    def apply_preemphasis(audio_bytes: bytes) -> bytes:
        """
        Apply pre-emphasis filter to boost high frequencies.
        Uses PREEMPHASIS_ALPHA from config.
        """
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        
        # Pre-emphasis: y[n] = x[n] - alpha * x[n-1]
        emphasized = np.append(audio_float[0], audio_float[1:] - PREEMPHASIS_ALPHA * audio_float[:-1])
        
        # Convert back to int16
        emphasized = np.clip(emphasized * 32768.0, -32768, 32767)
        return emphasized.astype(np.int16).tobytes()
    
    def calculate_rms_energy(audio_bytes: bytes) -> float:
        """Calculate RMS energy of audio chunk."""
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0
        return np.sqrt(np.mean(audio_float ** 2))
    
    if DEBUG_PRINT_AUDIO_STATS:
        print(f"[{session_id}] 📊 VAD Config:")
        print(f"   Silence timeout: {VAD_SILENCE_TIMEOUT_MS}ms ({SILENT_CHUNKS_FOR_EOS} chunks)")
        print(f"   Min speech duration: {MIN_SPEECH_DURATION_MS}ms ({MIN_SPEECH_CHUNKS} chunks)")
        print(f"   Barge-in threshold: {MIN_BARGEIN_SPEECH_CHUNKS} chunks")
        print(f"   VAD threshold: {VAD_SPEECH_THRESHOLD}")
        print(f"   Energy threshold: {MIN_AUDIO_ENERGY}")
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg['type'] == 'hangup':
                print(f"[{session_id}] 📞 Hangup received")
                await transcript_queue.put(None)
                break
            
            if msg['type'] != 'audio_data' or msg.get('format') != 'pcm16k':
                continue
            
            pcm16k_chunk = base64.b64decode(msg['audio'])
            
            # Apply pre-emphasis filter
            pcm16k_chunk = apply_preemphasis(pcm16k_chunk)
            
            pcm16k_buffer.extend(pcm16k_chunk)

            # Process in VAD-sized chunks
            while len(pcm16k_buffer) >= VAD_CHUNK_BYTES:
                current_chunk = pcm16k_buffer[:VAD_CHUNK_BYTES]
                pcm16k_buffer = pcm16k_buffer[VAD_CHUNK_BYTES:]
                
                # Log VAD input if enabled
                save_audio_chunk(current_chunk, session_id, "vad_input")
                
                # Quality check: Ensure chunk has sufficient energy
                energy = calculate_rms_energy(current_chunk)
                
                if energy < MIN_AUDIO_ENERGY:
                    # Too quiet - likely silence or noise
                    if DEBUG_PRINT_VAD_DECISIONS:
                        print(f"[{session_id}] 🔇 Low energy: {energy:.4f}")
                    
                    if is_speaking:
                        silent_chunks += 1
                        speech_buffer_pcm.extend(current_chunk)
                        if silent_chunks >= SILENT_CHUNKS_FOR_EOS:
                            # End of speech
                            is_speaking = False
                            await process_speech_buffer(
                                speech_buffer_pcm, session_id, websocket, 
                                transcript_queue, speech_chunks, MIN_SPEECH_CHUNKS
                            )
                            speech_buffer_pcm.clear()
                            speech_chunks = 0
                            silent_chunks = 0
                    continue
                
                # Run VAD
                is_speech = vad.is_chunk_speech(current_chunk)
                
                if DEBUG_PRINT_VAD_DECISIONS:
                    print(f"[{session_id}] VAD: {'🗣️' if is_speech else '🤐'} energy={energy:.4f}")
                
                if is_speech:
                    # Speech detected
                    if agent_is_speaking_event.is_set():
                        consecutive_speech_during_agent += 1
                        
                        # BARGE-IN: Need multiple consecutive speech chunks
                        if consecutive_speech_during_agent >= MIN_BARGEIN_SPEECH_CHUNKS:
                            if not interruption_event.is_set():
                                print(f"[{session_id}] 💥 BARGE-IN ({consecutive_speech_during_agent} chunks, {consecutive_speech_during_agent * MS_PER_VAD_CHUNK:.0f}ms)")
                                interruption_event.set()
                    else:
                        consecutive_speech_during_agent = 0
                    
                    if not is_speaking:
                        if DEBUG_PRINT_AUDIO_STATS:
                            print(f"[{session_id}] 🎤 User speaking (energy: {energy:.4f})")
                        is_speaking = True
                        speech_buffer_pcm.clear()
                        speech_chunks = 0
                    
                    speech_buffer_pcm.extend(current_chunk)
                    speech_chunks += 1
                    silent_chunks = 0
                
                elif is_speaking:
                    # Silence during speech
                    silent_chunks += 1
                    speech_buffer_pcm.extend(current_chunk)
                    consecutive_speech_during_agent = 0
                    
                    if silent_chunks >= SILENT_CHUNKS_FOR_EOS:
                        if DEBUG_PRINT_AUDIO_STATS:
                            duration_ms = speech_chunks * MS_PER_VAD_CHUNK
                            print(f"[{session_id}] 🛑 End of speech ({speech_chunks} chunks, {duration_ms:.0f}ms)")
                        is_speaking = False
                        
                        await process_speech_buffer(
                            speech_buffer_pcm, session_id, websocket, 
                            transcript_queue, speech_chunks, MIN_SPEECH_CHUNKS
                        )
                        
                        speech_buffer_pcm.clear()
                        speech_chunks = 0
                        silent_chunks = 0
                else:
                    # Silence while not speaking
                    consecutive_speech_during_agent = 0
                    silent_chunks = 0

    except WebSocketDisconnect:
        print(f"[{session_id}] 🔌 Receiver disconnected")
    except asyncio.CancelledError:
        print(f"[{session_id}] Receiver cancelled")
    except Exception as e:
        print(f"[{session_id}] ❌ Receiver error: {e}")
        traceback.print_exc()
    finally:
        await transcript_queue.put(None)


async def process_speech_buffer(
    speech_buffer: bytearray,
    session_id: str,
    websocket: WebSocket,
    transcript_queue: asyncio.Queue,
    speech_chunks: int,
    min_chunks: int
):
    """
    Process accumulated speech buffer.
    Validates minimum duration before transcription.
    """
    if speech_chunks < min_chunks:
        duration_ms = speech_chunks * MS_PER_VAD_CHUNK
        print(f"[{session_id}] ⏭️  Speech too short ({speech_chunks} chunks, {duration_ms:.0f}ms < {MIN_SPEECH_DURATION_MS}ms), ignoring")
        return
    
    duration_ms = speech_chunks * MS_PER_VAD_CHUNK
    
    if DEBUG_PRINT_AUDIO_STATS:
        print(f"[{session_id}] 🎯 Transcribing {len(speech_buffer)} bytes ({speech_chunks} chunks, {duration_ms:.0f}ms)")
    
    # Log whisper input if enabled
    save_audio_chunk(bytes(speech_buffer), session_id, "whisper_input")
    
    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(
        None, transcribe_audio, bytes(speech_buffer), "pcm16k"
    )
    
    if transcript.strip():
        print(f"[{session_id}] 📝 User: '{transcript}'")
        await websocket.send_json({
            "type": "transcript",
            "text": transcript
        })
        await transcript_queue.put(transcript)
    else:
        print(f"[{session_id}] 🔇 No speech detected by Whisper (possible noise)")


@router.websocket("/ws/vicidial/{session_id}")
async def websocket_vicidial(
    websocket: WebSocket, 
    session_id: str,
    agent: MedicareAgent = Depends(get_agent_manager)
):
    await websocket.accept()
    print(f"[{session_id}] 🔗 WebSocket connected")
    
    if ENABLE_AUDIO_LOGGING:
        print(f"[{session_id}] 🎙️  Audio logging ENABLED → {AUDIO_LOG_DIR.absolute()}")
    
    caller_id = f"{websocket.client.host}:{websocket.client.port}"
    agent._get_buffer(session_id)["caller_id"] = caller_id
    
    transcript_queue = asyncio.Queue()
    audio_queue = asyncio.Queue()
    interruption_event = asyncio.Event()
    agent_is_speaking_event = asyncio.Event()
    
    tasks = []
    try:
        sender_task = asyncio.create_task(
            audio_sender_task(
                websocket, audio_queue, interruption_event,
                agent_is_speaking_event, session_id
            )
        )
        tasks.append(sender_task)
        
        handler_task = asyncio.create_task(
            agent_handler_task(
                session_id, transcript_queue, audio_queue, 
                agent_is_speaking_event, interruption_event
            )
        )
        tasks.append(handler_task)
        
        receiver_task = asyncio.create_task(
            audio_receiver_task(
                websocket, session_id, transcript_queue, 
                agent_is_speaking_event, interruption_event
            )
        )
        tasks.append(receiver_task)
        
        print(f"[{session_id}] 🎤 Sending greeting...")
        await transcript_queue.put("")
        
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
    except Exception as e:
        print(f"[{session_id}] WebSocket error: {e}")
        traceback.print_exc()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        agent.end_call(session_id, "completed")
        
        # Clean up audio counters
        if session_id in _audio_counters:
            del _audio_counters[session_id]
        
        print(f"[{session_id}] Connection closed")
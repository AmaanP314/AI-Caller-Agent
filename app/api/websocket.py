import asyncio
import json
import base64
import traceback
import audioop # <-- IMPORT audioop FOR RESAMPLING
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.streaming.manager import MedicareAgent
from app.api.http import get_agent_manager # Reuse the dependency
from app.audio.stt import transcribe_audio
from app.audio import vad
from app.streaming.pipeline import llm_producer, tts_consumer
from app.config import VAD_SILENCE_TIMEOUT_MS
from app.audio.utils import resample_pcm8k_to_pcm16k_scipy
router = APIRouter()

# --- Helper to clear queues on interruption ---
async def clear_async_queue(q: asyncio.Queue):
    """Remove all items from an asyncio Queue."""
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except asyncio.QueueEmpty:
            break

# --- Task 1: The "Mouth" (Audio Sender) ---
async def audio_sender_task(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    session_id: str
):
    """
    Pulls audio from the audio_queue and sends it over the WebSocket.
    """
    try:
        while True:
            audio_chunk = await audio_queue.get()
            
            if audio_chunk is None:
                print(f"[{session_id}] Audio sender: TTS turn complete (ignoring sentinel).")
                audio_queue.task_done()
                continue

            chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
            
            # --- CHANGE 1: Tell relay this is pcm8k ---
            await websocket.send_json({
                "type": "audio_response",
                "audio": chunk_b64,
                "format": "pcm8k" 
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


# --- Task 2: The "Brain" (Agent Handler) ---
async def agent_handler_task(
    session_id: str,
    transcript_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    agent_is_speaking_event: asyncio.Event,
    interruption_event: asyncio.Event
):
    """
    Waits for transcripts, runs the LLM/TTS pipeline, and speaks.
    """
    try:
        while True:
            transcript = await transcript_queue.get()
            if transcript is None:
                break 

            interruption_event.clear()
            agent_is_speaking_event.set()
            
            sentence_queue = asyncio.Queue()
            
            producer_task = asyncio.create_task(
                llm_producer(session_id, transcript, sentence_queue, interruption_event)
            )
            
            # --- CHANGE 2: Request pcm8k instead of mulaw ---
            consumer_task = asyncio.create_task(
                tts_consumer(sentence_queue, audio_queue, interruption_event, output_format="pcm8k")
            )

            interruption_wait_task = asyncio.create_task(
                interruption_event.wait()
            )
            
            done, pending = await asyncio.wait(
                [producer_task, interruption_wait_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            if interruption_wait_task in done:
                print(f"[{session_id}] Agent handler: Interruption detected!")
                producer_task.cancel()
                consumer_task.cancel()
                await clear_async_queue(sentence_queue)
                await clear_async_queue(audio_queue)
            
            else:
                print(f"[{session_id}] Agent handler: LLM finished, waiting for TTS.")
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
        print(f"[{session_id}] Agent handler task exiting.")


async def audio_receiver_task(
    websocket: WebSocket,
    session_id: str,
    transcript_queue: asyncio.Queue,
    agent_is_speaking_event: asyncio.Event,
    interruption_event: asyncio.Event
):
    """
    Receives pcm8k, buffers it, resamples to pcm16k using Scipy,
    runs VAD, and handles endpointing.
    """
    vad_model, vad_utils = vad.get_vad_model()
    if vad_model is None:
        print(f"[{session_id}] VAD model not loaded. Receiver task cannot start.")
        return

    # VAD State
    pcm16k_buffer = bytearray() # Buffer for VAD-sized chunks
    speech_buffer_pcm = bytearray() # Buffer for full utterance
    is_speaking = False
    silent_chunks = 0
    
    # --- NEW: Buffer for 8k->16k resampling ---
    pcm8k_resample_buffer = bytearray()
    RESAMPLE_BATCH_MS = 100 # Resample in 100ms batches
    RESAMPLE_BATCH_BYTES = int(8000 * 2 * (RESAMPLE_BATCH_MS / 1000)) # 8000*2*0.1 = 1600 bytes
    
    MS_PER_VAD_CHUNK = (vad.VAD_CHUNK_SAMPLES / vad.VAD_SAMPLE_RATE) * 1000 # 32ms
    SILENT_CHUNKS_FOR_EOS = int(VAD_SILENCE_TIMEOUT_MS / MS_PER_VAD_CHUNK) # ~31
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg['type'] == 'hangup':
                print(f"[{session_id}] 📞 Hangup received in receiver")
                await transcript_queue.put(None)
                break
            
            if msg['type'] != 'audio_data':
                continue
            
            if msg.get('format') != 'pcm8k':
                print(f"[{session_id}] ⚠️ Received wrong format: {msg.get('format')}, skipping")
                continue
                
            pcm8k_chunk = base64.b64decode(msg['audio'])
            pcm8k_resample_buffer.extend(pcm8k_chunk)

            # --- NEW: Resample in 100ms batches ---
            while len(pcm8k_resample_buffer) >= RESAMPLE_BATCH_BYTES:
                # 1. Get 100ms batch of pcm8k
                pcm8k_batch = pcm8k_resample_buffer[:RESAMPLE_BATCH_BYTES]
                pcm8k_resample_buffer = pcm8k_resample_buffer[RESAMPLE_BATCH_BYTES:]
                
                # 2. Resample 8k -> 16k using scipy
                loop = asyncio.get_event_loop()
                pcm16k_batch = await loop.run_in_executor(
                    None, resample_pcm8k_to_pcm16k_scipy, pcm8k_batch
                )
                
                if not pcm16k_batch:
                    continue # Resampling failed
                
                # 3. Add resampled audio to VAD buffer
                pcm16k_buffer.extend(pcm16k_batch)

            # --- VAD Logic (unchanged) ---
            while len(pcm16k_buffer) >= vad.VAD_CHUNK_BYTES:
                current_chunk_pcm = pcm16k_buffer[:vad.VAD_CHUNK_BYTES]
                pcm16k_buffer = pcm16k_buffer[vad.VAD_CHUNK_BYTES:]
                
                is_speech = vad.is_chunk_speech(current_chunk_pcm)
                
                if is_speech and agent_is_speaking_event.is_set() and not interruption_event.is_set():
                    print(f"[{session_id}] 💥 BARGE-IN DETECTED!")
                    interruption_event.set()
                
                if is_speech:
                    if not is_speaking:
                        print(f"[{session_id}] 🎤 User started speaking...")
                        is_speaking = True
                        speech_buffer_pcm.clear()
                    
                    speech_buffer_pcm.extend(current_chunk_pcm)
                    silent_chunks = 0
                
                elif not is_speech and is_speaking:
                    silent_chunks += 1
                    speech_buffer_pcm.extend(current_chunk_pcm)
                    
                    if silent_chunks >= SILENT_CHUNKS_FOR_EOS:
                        print(f"[{session_id}] 🛑 End of speech detected.")
                        is_speaking = False
                        
                        loop = asyncio.get_event_loop()
                        transcript = await loop.run_in_executor(
                            None, transcribe_audio, bytes(speech_buffer_pcm), "pcm16k"
                        )
                        speech_buffer_pcm.clear()
                        
                        if transcript.strip():
                            print(f"[{session_id}] 📝 User: {transcript}")
                            await websocket.send_json({
                                "type": "transcript",
                                "text": transcript
                            })
                            await transcript_queue.put(transcript)
                        else:
                             print(f"[{session_id}] 🔇 VAD triggered, but Whisper found no text.")
                
                elif not is_speech and not is_speaking:
                    silent_chunks = 0
                    speech_buffer_pcm.clear()

    except WebSocketDisconnect:
        print(f"[{session_id}] 🔌 Receiver disconnected.")
    except asyncio.CancelledError:
        print(f"[{session_id}] Receiver cancelled.")
    except Exception as e:
        print(f"[{session_id}] ❌ Receiver error: {e}")
        traceback.print_exc()
    finally:
        print(f"[{session_id}] Receiver task exiting.")
        await transcript_queue.put(None)

# --- The Main WebSocket Endpoint ---
@router.websocket("/ws/vicidial/{session_id}")
async def websocket_vicidial(
    websocket: WebSocket, 
    session_id: str,
    agent: MedicareAgent = Depends(get_agent_manager)
):
    await websocket.accept()
    print(f"[{session_id}] 🔗 WebSocket connected with VAD/Interruption")
    
    caller_id = f"{websocket.client.host}:{websocket.client.port}"
    # --- CHANGE 7: Pass caller_id to _get_buffer ---
    # (Assuming _get_buffer is in your manager.py, add caller_id param)
    # agent._get_buffer(session_id, caller_id=caller_id) 
    # If not, this line can be:
    agent._get_buffer(session_id)["caller_id"] = caller_id

    
    transcript_queue = asyncio.Queue()
    audio_queue = asyncio.Queue()
    interruption_event = asyncio.Event()
    agent_is_speaking_event = asyncio.Event()
    
    tasks = []
    try:
        sender_task = asyncio.create_task(
            audio_sender_task(websocket, audio_queue, session_id)
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
        
        print(f"[{session_id}] Main loop exiting, a task has completed.")
        
    except Exception as e:
        print(f"[{session_id}] WebSocket main error: {e}")
        traceback.print_exc()
    finally:
        print(f"[{session_id}] Cleaning up all tasks...")
        for task in tasks:
            if not task.done():
                task.cancel()
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        agent.end_call(session_id, "completed")
        print(f"[{session_id}] Connection closed and call saved.")
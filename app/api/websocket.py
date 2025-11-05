import asyncio
import json
import base64
import traceback
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.streaming.manager import MedicareAgent
from app.api.http import get_agent_manager # Reuse the dependency
from app.audio.stt import transcribe_audio
from app.audio import vad
# --- CHANGE 1: Remove mulaw converter ---
# from app.audio.utils import convert_mulaw_chunk_to_pcm16k
from app.streaming.pipeline import llm_producer, tts_consumer
from app.config import VAD_SILENCE_TIMEOUT_MS

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
    This runs continuously for the entire call.
    """
    try:
        while True:
            audio_chunk = await audio_queue.get()
            
            if audio_chunk is None:
                print(f"[{session_id}] Audio sender: TTS turn complete (ignoring sentinel).")
                audio_queue.task_done()
                continue

            chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
            
            # --- ADD sample_rate field ---
            await websocket.send_json({
                "type": "audio_response",
                "audio": chunk_b64,
                "sample_rate": 8000,  # ← ADD THIS LINE
                "format": "pcm8k"      # ← ADD THIS LINE (optional but helpful)
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
    Can be interrupted.
    """
    try:
        while True:
            # 1. Wait for a transcript from the "Ears"
            transcript = await transcript_queue.get()
            if transcript is None:
                break # Shutdown signal

            # 2. Set flags: "I'm about to talk"
            interruption_event.clear()
            agent_is_speaking_event.set()
            
            # 3. Create pipeline components for this turn
            sentence_queue = asyncio.Queue()
            
            producer_task = asyncio.create_task(
                llm_producer(session_id, transcript, sentence_queue, interruption_event)
            )
            
            # --- CHANGE 2: Request pcm8k instead of mulaw ---
            consumer_task = asyncio.create_task(
                tts_consumer(sentence_queue, audio_queue, interruption_event, output_format="pcm8k")
            )

            # 4. Wait for LLM to finish OR for an interruption
            interruption_wait_task = asyncio.create_task(
                interruption_event.wait()
            )
            
            done, pending = await asyncio.wait(
                [producer_task, interruption_wait_task], # Pass both TASKS
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 5. Handle the result
            if interruption_wait_task in done:
                # Interruption happened!
                print(f"[{session_id}] Agent handler: Interruption detected!")
                
                # Cancel producer and consumer
                producer_task.cancel()
                consumer_task.cancel()
                
                # Clear queues to discard pending items
                await clear_async_queue(sentence_queue)
                await clear_async_queue(audio_queue)
            
            else:
                # LLM finished normally.
                print(f"[{session_id}] Agent handler: LLM finished, waiting for TTS.")
                
                # We must cancel the interruption_wait_task,
                interruption_wait_task.cancel()
                
                # We must wait for the consumer task to drain the sentence_queue
                await consumer_task

            # 6. Clear flags: "I'm done talking"
            agent_is_speaking_event.clear()
            transcript_queue.task_done()
            
    except asyncio.CancelledError:
        print(f"[{session_id}] Agent handler cancelled.")
    except Exception as e:
        print(f"[{session_id}] Agent handler error: {e}")
        traceback.print_exc()
    finally:
        # Ensure flag is cleared on exit
        agent_is_speaking_event.clear()
        print(f"[{session_id}] Agent handler task exiting.")


# --- Task 3: The "Ears" (Audio Receiver + VAD) ---
async def audio_receiver_task(
    websocket: WebSocket,
    session_id: str,
    transcript_queue: asyncio.Queue,
    agent_is_speaking_event: asyncio.Event,
    interruption_event: asyncio.Event
):
    """
    Receives all audio from the user, runs VAD, and handles
    endpointing (Step 3) and interruption (Step 4).
    """
    vad_model, vad_utils = vad.get_vad_model()
    if vad_model is None:
        print(f"[{session_id}] VAD model not loaded. Receiver task cannot start.")
        return

    # VAD State
    # --- CHANGE 3: Remove ratecv_state ---
    # ratecv_state = None
    pcm16k_buffer = bytearray()
    speech_buffer_pcm = bytearray()
    is_speaking = False
    silent_chunks = 0
    
    # VAD timing constants
    MS_PER_CHUNK = (vad.VAD_CHUNK_SAMPLES / vad.VAD_SAMPLE_RATE) * 1000 # e.g., 32ms
    SILENT_CHUNKS_FOR_EOS = int(VAD_SILENCE_TIMEOUT_MS / MS_PER_CHUNK) # e.g., 1000 / 32 = ~31
    
    try:
        while True:
            # 1. Get audio from client
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg['type'] == 'hangup':
                print(f"[{session_id}] 📞 Hangup received in receiver")
                await transcript_queue.put(None) # Signal handler to shut down
                break
            
            if msg['type'] != 'audio_data':
                continue
            
            # --- CHANGE 4: Expect pcm16k, remove conversion ---
            if msg.get('format') != 'pcm16k':
                print(f"[{session_id}] ⚠️ Received wrong format: {msg.get('format')}, skipping")
                continue
                
            pcm16k_chunk = base64.b64decode(msg['audio'])
            print(f"[{session_id}] 📥 Received {len(pcm16k_chunk)} bytes pcm16k")

            # We no longer need this conversion:
            # mulaw_chunk = base64.b64decode(msg['audio'])
            # pcm16k_chunk, ratecv_state = convert_mulaw_chunk_to_pcm16k(mulaw_chunk, ratecv_state)
            
            # The relay will send 640-byte chunks (20ms @ 16k)
            # We buffer them to get 1024-byte chunks (32ms @ 16k) for VAD
            pcm16k_buffer.extend(pcm16k_chunk)
            print(f"[{session_id}] 📊 Buffer now has {len(pcm16k_buffer)} bytes (need {vad.VAD_CHUNK_BYTES})")


            # 3. Process buffer in VAD-sized chunks
            while len(pcm16k_buffer) >= vad.VAD_CHUNK_BYTES:
                current_chunk_pcm = pcm16k_buffer[:vad.VAD_CHUNK_BYTES]
                pcm16k_buffer = pcm16k_buffer[vad.VAD_CHUNK_BYTES:]
                
                # 4. Run VAD
                is_speech = vad.is_chunk_speech(current_chunk_pcm)
                
                # --- START UNIFIED LOGIC ---
                
                # 5. Interruption Check (runs in parallel to endpointing)
                #    If user speaks WHILE agent is speaking, set the flag.
                if is_speech and agent_is_speaking_event.is_set() and not interruption_event.is_set():
                    print(f"[{session_id}] 💥 BARGE-IN DETECTED!")
                    interruption_event.set() # Signal the Brain
                
                # 6. Endpointing Logic (always runs)
                if is_speech:
                    if not is_speaking:
                        # This is the start of a new utterance
                        # (either normal or an interruption)
                        print(f"[{session_id}] 🎤 User started speaking...")
                        is_speaking = True
                        speech_buffer_pcm.clear()
                    
                    speech_buffer_pcm.extend(current_chunk_pcm)
                    silent_chunks = 0
                
                elif not is_speech and is_speaking:
                    # User was speaking, but this chunk is silent
                    silent_chunks += 1
                    # --- ADDED THIS LINE ---
                    # We should still buffer the silence in case speech resumes
                    speech_buffer_pcm.extend(current_chunk_pcm)
                    
                    if silent_chunks >= SILENT_CHUNKS_FOR_EOS:
                        print(f"[{session_id}] 🛑 End of speech detected.")
                        is_speaking = False
                        
                        # Transcribe the full buffer
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
                            # Send transcript to the "Brain"
                            await transcript_queue.put(transcript)
                        else:
                             print(f"[{session_id}] 🔇 VAD triggered, but Whisper found no text.")
                
                elif not is_speech and not is_speaking:
                    # Standard silence, reset buffer
                    silent_chunks = 0
                    # We clear the buffer to prevent old audio from
                    # being transcribed on the next utterance.
                    speech_buffer_pcm.clear()
                    
                # --- END UNIFIED LOGIC ---

    except WebSocketDisconnect:
        print(f"[{session_id}] 🔌 Receiver disconnected.")
    except asyncio.CancelledError:
        print(f"[{session_id}] Receiver cancelled.")
    except Exception as e:
        print(f"[{session_id}] ❌ Receiver error: {e}")
        traceback.print_exc()
    finally:
        # Signal other tasks to shut down
        print(f"[{session_id}] Receiver task exiting.")
        await transcript_queue.put(None)

# --- The Main WebSocket Endpoint ---
@router.websocket("/ws/vicidial/{session_id}")
async def websocket_vicidial(
    websocket: WebSocket, 
    session_id: str,
    agent: MedicareAgent = Depends(get_agent_manager)
):
    """
    Main WebSocket endpoint with VAD (Step 3) and Interruption (Step 4).
    Manages the three concurrent tasks.
    """
    await websocket.accept()
    print(f"[{session_id}] 🔗 WebSocket connected with VAD/Interruption")
    
    # 1. Get/create call buffer
    caller_id = f"{websocket.client.host}:{websocket.client.port}"
    # --- CHANGE 5: Pass caller_id to _get_buffer ---
    agent._get_buffer(session_id, caller_id=caller_id)
    
    # 2. Create communication channels
    transcript_queue = asyncio.Queue()
    audio_queue = asyncio.Queue()
    interruption_event = asyncio.Event()
    agent_is_speaking_event = asyncio.Event()
    
    # 3. Launch the three main tasks
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
        
        # 4. Send the greeting
        print(f"[{session_id}] 🎤 Sending greeting...")
        await transcript_queue.put("") # Empty string triggers greeting
        
        # 5. Wait for any task to fail or disconnect
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        print(f"[{session_id}] Main loop exiting, a task has completed.")
        
    except Exception as e:
        print(f"[{session_id}] WebSocket main error: {e}")
        traceback.print_exc()
    finally:
        # 6. Cleanup
        print(f"[{session_id}] Cleaning up all tasks...")
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to *actually* cancel
        await asyncio.gather(*tasks, return_exceptions=True)
        
        agent.end_call(session_id, "completed")
        print(f"[{session_id}] Connection closed and call saved.")
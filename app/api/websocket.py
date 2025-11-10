import asyncio
import json
import base64
import traceback
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.streaming.manager import MedicareAgent
from app.api.http import get_agent_manager
from app.audio.stt import transcribe_audio
from app.audio import vad
from app.streaming.pipeline import llm_producer, tts_consumer
from app.config import VAD_SILENCE_TIMEOUT_MS

router = APIRouter()

async def clear_async_queue(q: asyncio.Queue):
    """Remove all items from an asyncio Queue."""
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except asyncio.QueueEmpty:
            break

# --- FIXED: Audio sender now respects interruption ---
async def audio_sender_task(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    interruption_event: asyncio.Event,
    session_id: str
):
    """
    Pulls audio from queue and sends over WebSocket.
    NOW CHECKS for interruption before sending each chunk.
    """
    try:
        while True:
            # Non-blocking check for interruption
            if interruption_event.is_set():
                # Drain any remaining audio from queue without sending
                while not audio_queue.empty():
                    try:
                        audio_queue.get_nowait()
                        audio_queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                
                # Send interrupt signal to relay
                await websocket.send_json({"type": "interrupt"})
                print(f"[{session_id}] 🛑 Audio sender: Interrupt signal sent to relay")
                
                # Clear the interruption flag after handling
                interruption_event.clear()
                continue
            
            # Get audio chunk (with timeout to allow periodic interruption checks)
            try:
                audio_chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue  # Check interruption flag again
            
            if audio_chunk is None:
                print(f"[{session_id}] Audio sender: TTS turn complete (ignoring sentinel).")
                audio_queue.task_done()
                continue

            # Final check before sending
            if interruption_event.is_set():
                audio_queue.task_done()
                continue

            chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
            await websocket.send_json({
                "type": "audio_response",
                "audio": chunk_b64,
                "format": "pcm16k",  # Relay will downsample
                "sample_rate": 16000
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
    """
    Waits for transcripts, runs LLM/TTS pipeline.
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
                print(f"[{session_id}] 🚨 INTERRUPTION DETECTED - Cancelling tasks")
                
                # Cancel both producer and consumer
                producer_task.cancel()
                consumer_task.cancel()
                
                # Clear queues
                await clear_async_queue(sentence_queue)
                await clear_async_queue(audio_queue)
                
                # Wait for tasks to finish cancelling
                try:
                    await asyncio.gather(producer_task, consumer_task, return_exceptions=True)
                except:
                    pass
                
                print(f"[{session_id}] ✅ Tasks cancelled, queues cleared")
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
    Receives pcm16k from relay, runs VAD, handles endpointing.
    """
    vad_model, vad_utils = vad.get_vad_model()
    if vad_model is None:
        print(f"[{session_id}] VAD model not loaded. Receiver task cannot start.")
        return

    # VAD State
    pcm16k_buffer = bytearray()
    speech_buffer_pcm = bytearray()
    is_speaking = False
    silent_chunks = 0
    
    MS_PER_VAD_CHUNK = (vad.VAD_CHUNK_SAMPLES / vad.VAD_SAMPLE_RATE) * 1000
    SILENT_CHUNKS_FOR_EOS = int(VAD_SILENCE_TIMEOUT_MS / MS_PER_VAD_CHUNK)
    
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
            
            if msg.get('format') != 'pcm16k':
                print(f"[{session_id}] ⚠️ Received wrong format: {msg.get('format')}, skipping")
                continue
                
            # Receive 16k audio directly from relay (already upsampled)
            pcm16k_chunk = base64.b64decode(msg['audio'])
            pcm16k_buffer.extend(pcm16k_chunk)

            # VAD processing
            while len(pcm16k_buffer) >= vad.VAD_CHUNK_BYTES:
                current_chunk_pcm = pcm16k_buffer[:vad.VAD_CHUNK_BYTES]
                pcm16k_buffer = pcm16k_buffer[vad.VAD_CHUNK_BYTES:]
                
                is_speech = vad.is_chunk_speech(current_chunk_pcm)
                
                # BARGE-IN DETECTION
                if is_speech and agent_is_speaking_event.is_set() and not interruption_event.is_set():
                    print(f"[{session_id}] 💥 BARGE-IN DETECTED - Setting interruption!")
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


@router.websocket("/ws/vicidial/{session_id}")
async def websocket_vicidial(
    websocket: WebSocket, 
    session_id: str,
    agent: MedicareAgent = Depends(get_agent_manager)
):
    await websocket.accept()
    print(f"[{session_id}] 🔗 WebSocket connected with VAD/Interruption")
    
    caller_id = f"{websocket.client.host}:{websocket.client.port}"
    agent._get_buffer(session_id)["caller_id"] = caller_id
    
    transcript_queue = asyncio.Queue()
    audio_queue = asyncio.Queue()
    interruption_event = asyncio.Event()
    agent_is_speaking_event = asyncio.Event()
    
    tasks = []
    try:
        # FIXED: Pass interruption_event to audio sender
        sender_task = asyncio.create_task(
            audio_sender_task(websocket, audio_queue, interruption_event, session_id)
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
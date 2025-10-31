import asyncio
import json
import base64
import traceback
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.streaming.manager import MedicareAgent
from app.api.http import get_agent_manager # Reuse the dependency
from app.audio.stt import transcribe_audio
from app.audio.utils import mulaw_to_pcm16k_bytes
from app.streaming.pipeline import llm_producer, tts_consumer

router = APIRouter()

# Define constants for audio
MULAW_CHUNK_SIZE = 4000 # 0.5 seconds of 8kHz mulaw audio (8000 bytes/s)

async def audio_sender_task(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    session_id: str
):
    """
    A single, long-running task that pulls audio from the queue
    and sends it over the WebSocket.
    """
    try:
        while True:
            audio_chunk = await audio_queue.get()
            if audio_chunk is None:
                print(f"[{session_id}] Audio sender received sentinel, closing.")
                break
            
            # Convert audio chunk (which is mulaw bytes) to base64
            chunk_b64 = base64.b64encode(audio_chunk).decode('utf-8')
            
            await websocket.send_json({
                "type": "audio_response",
                "audio": chunk_b64
            })
            audio_queue.task_done()
            
    except WebSocketDisconnect:
        print(f"[{session_id}] Audio sender disconnected.")
    except Exception as e:
        print(f"[{session_id}] Audio sender error: {e}")
        traceback.print_exc()


@router.websocket("/ws/vicidial/{session_id}")
async def websocket_vicidial(
    websocket: WebSocket, 
    session_id: str,
    agent: MedicareAgent = Depends(get_agent_manager)
):
    """
    WebSocket for VICIdial/Asterisk integration with streaming.
    - Receives: mulaw audio chunks
    - Sends: mulaw audio chunks (streamed)
    """
    await websocket.accept()
    print(f"[{session_id}] 🔗 WebSocket connected for Telephony")
    
    # Get/create the call buffer and set caller ID
    caller_id = f"{websocket.client.host}:{websocket.client.port}"
    agent._get_buffer(session_id, caller_id=caller_id)
    
    # --- Setup Queues and Tasks ---
    sentence_queue = asyncio.Queue()
    audio_queue = asyncio.Queue()
    
    # Create the single audio sender task
    sender_task = asyncio.create_task(
        audio_sender_task(websocket, audio_queue, session_id)
    )
    
    # Create placeholder tasks for agent pipeline
    producer_task = None
    consumer_task = None
    
    # --- Buffers for STT ---
    # We use the simple 0.5s buffer for now. Step 3 will add VAD.
    temp_stt_buffer_mulaw = bytearray()

    try:
        # --- 1. Send Greeting ---
        print(f"[{session_id}] 🎤 Sending greeting...")
        producer_task = asyncio.create_task(
            llm_producer(session_id, "", sentence_queue)
        )
        consumer_task = asyncio.create_task(
            tts_consumer(sentence_queue, audio_queue, output_format="mulaw")
        )
        
        # --- 2. Main Receive Loop ---
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg['type'] == 'audio_data':
                mulaw_chunk = base64.b64decode(msg['audio'])
                temp_stt_buffer_mulaw.extend(mulaw_chunk)
                
                # Check if we have enough audio to transcribe
                while len(temp_stt_buffer_mulaw) >= MULAW_CHUNK_SIZE:
                    audio_to_process = bytes(temp_stt_buffer_mulaw[:MULAW_CHUNK_SIZE])
                    temp_stt_buffer_mulaw = temp_stt_buffer_mulaw[MULAW_CHUNK_SIZE:]
                    
                    # Transcribe in executor
                    loop = asyncio.get_event_loop()
                    transcript = await loop.run_in_executor(
                        None, transcribe_audio, audio_to_process, "mulaw"
                    )
                    
                    if transcript.strip():
                        print(f"[{session_id}] 📝 User: {transcript}")
                        await websocket.send_json({
                            "type": "transcript",
                            "text": transcript
                        })
                        
                        # Check if agent is busy
                        if producer_task and not producer_task.done():
                            print(f"[{session_id}] ⚠️ User spoke, but agent is still thinking. (Interruption logic in Step 4)")
                            # In Step 4, we will cancel the tasks here
                            continue
                        
                        # If agent is free, start the pipeline
                        print(f"[{session_id}] 🤖 Agent processing...")
                        sentence_queue = asyncio.Queue() # Create new queue for this turn
                        producer_task = asyncio.create_task(
                            llm_producer(session_id, transcript, sentence_queue)
                        )
                        consumer_task = asyncio.create_task(
                            tts_consumer(sentence_queue, audio_queue, output_format="mulaw")
                        )

            elif msg['type'] == 'hangup':
                print(f"[{session_id}] 📞 Hangup received")
                agent.end_call(session_id, "completed_hangup")
                break
                
    except WebSocketDisconnect:
        print(f"[{session_id}] 🔌 WebSocket disconnected")
        agent.end_call(session_id, "disconnected")
    except Exception as e:
        print(f"[{session_id}] ❌ Error: {e}")
        traceback.print_exc()
        agent.end_call(session_id, "error")
    finally:
        # --- Cleanup ---
        print(f"[{session_id}] Cleaning up tasks...")
        if producer_task and not producer_task.done():
            producer_task.cancel()
        if consumer_task and not consumer_task.done():
            consumer_task.cancel()
        
        # Send sentinel to audio sender to make it stop
        if audio_queue:
            await audio_queue.put(None)
        if sender_task and not sender_task.done():
            sender_task.cancel()


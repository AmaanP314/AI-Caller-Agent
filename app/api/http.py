import asyncio
import traceback
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse
from app.audio.stt import transcribe_audio
from app.streaming.pipeline import llm_producer, tts_consumer, audio_chunk_streamer
from app.streaming.manager import MedicareAgent

# This will be initialized in main.py and injected
agent_manager_instance = None

def set_agent_manager(manager: MedicareAgent):
    global agent_manager_instance
    agent_manager_instance = manager

def get_agent_manager() -> MedicareAgent:
    if agent_manager_instance is None:
        raise HTTPException(status_code=500, detail="Agent manager not initialized")
    return agent_manager_instance

# Create router
router = APIRouter()

@router.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "message": "Medicare AI Voice Agent is active."
    }

@router.post("/api/voice-message-streaming")
async def process_voice_message_streaming(
    audio: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    agent: MedicareAgent = Depends(get_agent_manager)
):
    """
    Process voice message with sentence-level streaming.
    This is your test endpoint from the second app.py.
    """
    try:
        if not session_id:
            session_id = f"test_session_{asyncio.get_event_loop().time()}"

        # 1. Transcribe (blocking)
        audio_bytes = await audio.read()
        user_text = await asyncio.get_event_loop().run_in_executor(
            None, transcribe_audio, audio_bytes
        )
        print(f"[{session_id}] User said: {user_text}")
        
        # 2. Create queues
        sentence_queue = asyncio.Queue(maxsize=10)
        audio_queue = asyncio.Queue(maxsize=5)
        
        # 3. Start concurrent tasks
        producer_task = asyncio.create_task(
            llm_producer(session_id, user_text, sentence_queue)
        )
        consumer_task = asyncio.create_task(
            tts_consumer(sentence_queue, audio_queue)
        )
        
        # 4. Stream audio chunks back
        return StreamingResponse(
            audio_chunk_streamer(audio_queue),
            media_type="audio/wav",
            headers={
                "X-Session-Id": session_id,
                "X-Transcript": user_text.replace('\n', ' ').replace('\r', ''),
            }
        )
        
    except Exception as e:
        print(f"[Voice Streaming Error] {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# --- Other useful HTTP endpoints from your first app.py ---

@router.post("/api/text-message")
async def text_message(request: dict, agent: MedicareAgent = Depends(get_agent_manager)):
    """Text-based message API for debugging"""
    session_id = request.get("session_id", "text_default")
    message = request.get("message", "")
    
    # Use the streaming processor, but just collect the response
    full_response = ""
    async for chunk in agent.process_message_streaming(session_id, message):
        if "sentence" in chunk:
            full_response += chunk["sentence"] + " "
    
    patient_info = agent.get_patient_info(session_id)
    
    return {
        "session_id": session_id,
        "agent_response": full_response.strip(),
        "patient_info": patient_info
    }


@router.post("/api/end-call/{session_id}")
async def end_call_endpoint(session_id: str, agent: MedicareAgent = Depends(get_agent_manager)):
    """End call and save to database"""
    agent.end_call(session_id, "completed_by_api")
    return {"status": "success", "session_id": session_id}


@router.get("/api/patient-info/{session_id}")
async def get_patient_info_endpoint(session_id: str, agent: MedicareAgent = Depends(get_agent_manager)):
    """Get patient information"""
    patient_info = agent.get_patient_info(session_id)
    return {"session_id": session_id, "patient_info": patient_info}

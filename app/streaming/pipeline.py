import asyncio
import traceback
from typing import AsyncGenerator
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage
from app.config import GOOGLE_API_KEY
from app.agent.state import InterviewState, PatientInfoExtraction
from app.agent.prompts import build_system_prompt
from app.agent.tools import update_patient_info, end_call, forward_call_to_human
from app.streaming.buffer import SentenceBuffer
from app.audio.tts import synthesize_speech_for_pipeline # Updated import

# This will be initialized in main.py
agent_manager = None

def set_agent_manager(manager):
    """Inject the agent manager dependency."""
    global agent_manager
    agent_manager = manager

# ===== PHASE 1: STREAMING AGENT NODE =====
async def agent_node_streaming(state: InterviewState) -> AsyncGenerator[dict, None]:
    """
    Streaming version of agent_node that yields sentence-level updates.
    """
    messages = state["messages"]
    patient_info = state.get("patient_info") or PatientInfoExtraction()
    relevant_history = messages[-6:]
    
    system_prompt = build_system_prompt(patient_info, has_messages=bool(messages))
    
    tools = [update_patient_info, end_call, forward_call_to_human]
    model = ChatGoogleGenerativeAI(
        temperature=0.7,
        model="gemini-flash-lite-latest",
        api_key=GOOGLE_API_KEY
    ).bind_tools(tools)
    
    sentence_buffer = SentenceBuffer(min_words=10)
    full_response_content = ""
    tool_calls = []
    
    try:
        async for chunk in model.astream([system_prompt] + relevant_history):
            if hasattr(chunk, 'content') and chunk.content:
                token = chunk.content
                full_response_content += token
                complete_sentence = sentence_buffer.add_token(token)
                
                if complete_sentence:
                    yield {"sentence": complete_sentence, "type": "sentence"}
            
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)
    
    except Exception as e:
        print(f"Error during LLM stream: {e}")
        traceback.print_exc()
        
    final_sentence = sentence_buffer.mark_final()
    if final_sentence:
        yield {"sentence": final_sentence, "type": "sentence"}
        
    final_message = AIMessage(
        content=full_response_content,
        tool_calls=tool_calls if tool_calls else []
    )
    
    yield {
        "messages": [final_message],
        "type": "final"
    }

# ===== PHASE 3: ASYNCIO QUEUE PIPELINE =====

async def llm_producer(
    session_id: str,
    user_message: str,
    sentence_queue: asyncio.Queue
):
    """
    Producer: Streams sentences from LLM and puts them in the queue.
    """
    if agent_manager is None:
        print("Error: Agent Manager not set")
        await sentence_queue.put(None)
        return

    try:
        async for chunk in agent_manager.process_message_streaming(session_id, user_message):
            if "sentence" in chunk:
                sentence = chunk["sentence"]
                print(f"[LLM→Queue] Sentence: {sentence[:50]}...")
                await sentence_queue.put(sentence)
            
            elif chunk.get("final"):
                print("[LLM→Queue] Streaming complete, sending sentinel")
                await sentence_queue.put(None)
                break
                
    except Exception as e:
        print(f"[LLM Producer Error] {e}")
        traceback.print_exc()
        await sentence_queue.put(None) # Send sentinel on error


async def tts_consumer(
    sentence_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    output_format: str = "wav" # Added output_format
):
    """
    Consumer: Takes sentences, synthesizes audio, and puts chunks in audio queue.
    """
    try:
        while True:
            sentence = await sentence_queue.get()
            
            if sentence is None:
                print("[Queue→TTS] Received sentinel, ending synthesis")
                sentence_queue.task_done()
                await audio_queue.put(None) # Pass sentinel to audio queue
                break
                
            print(f"[Queue→TTS] Synthesizing for {output_format}: {sentence[:50]}...")
            
            # Run blocking TTS in thread pool
            loop = asyncio.get_event_loop()
            audio_bytes = await loop.run_in_executor(
                None, # Default thread pool
                synthesize_speech_for_pipeline,
                sentence,
                output_format # Pass the requested format
            )
            
            await audio_queue.put(audio_bytes)
            print(f"[TTS→Audio Queue] Chunk ready ({len(audio_bytes)} bytes)")
            
            sentence_queue.task_done()
            
    except Exception as e:
        print(f"[TTS Consumer Error] {e}")
        traceback.print_exc()
        await audio_queue.put(None)


async def audio_chunk_streamer(
    audio_queue: asyncio.Queue
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that yields audio chunks as they become available.
    (This is for the HTTP endpoint, not the WebSocket)
    """
    first_chunk = True
    try:
        while True:
            audio_chunk = await audio_queue.get()
            
            if audio_chunk is None:
                print("[Audio Streamer] Stream complete")
                audio_queue.task_done()
                break
                
            if first_chunk:
                print(f"[Audio Streamer] First chunk ready! ({len(audio_chunk)} bytes)")
                first_chunk = False
            
            yield audio_chunk
            audio_queue.task_done()
            
    except Exception as e:
        print(f"[Audio Streamer Error] {e}")
        traceback.print_exc()


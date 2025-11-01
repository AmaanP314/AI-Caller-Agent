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
from app.audio.tts import synthesize_speech_for_pipeline

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
    
    except asyncio.CancelledError:
        print("[agent_node_streaming] Stream cancelled.")
        raise # Re-raise to be handled by producer
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
    sentence_queue: asyncio.Queue,
    interruption_event: asyncio.Event
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
            if interruption_event.is_set():
                print("[LLM Producer] Interruption detected, stopping.")
                break # Exit loop if interrupted
                
            if "sentence" in chunk:
                sentence = chunk["sentence"]
                print(f"[LLM→Queue] Sentence: {sentence[:50]}...")
                await sentence_queue.put(sentence)
            
            elif chunk.get("final"):
                # Don't send sentinel yet, wait for finally
                break
                
    except asyncio.CancelledError:
        print("[LLM Producer] Cancelled.")
    except Exception as e:
        print(f"[LLM Producer Error] {e}")
        traceback.print_exc()
    finally:
        # CRITICAL: Always send sentinel to shut down tts_consumer
        print("[LLM Producer] Sending sentinel to TTS.")
        await sentence_queue.put(None)


async def tts_consumer(
    sentence_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    interruption_event: asyncio.Event,
    output_format: str = "wav"
):
    """
    Consumer: Takes sentences, synthesizes audio, and puts chunks in audio queue.
    """
    try:
        while True:
            if interruption_event.is_set():
                print("[TTS Consumer] Interruption detected, stopping.")
                break
                
            # Wait for a sentence
            sentence = await sentence_queue.get()
            
            if sentence is None:
                print("[Queue→TTS] Received sentinel, ending synthesis")
                sentence_queue.task_done()
                break # Exit loop
                
            print(f"[Queue→TTS] Synthesizing for {output_format}: {sentence[:50]}...")
            
            # Run blocking TTS in thread pool
            loop = asyncio.get_event_loop()
            audio_bytes = await loop.run_in_executor(
                None, # Default thread pool
                synthesize_speech_for_pipeline,
                sentence,
                output_format
            )
            
            # Before putting in queue, check one last time
            if interruption_event.is_set():
                print("[TTS Consumer] Interrupted before sending audio chunk.")
                sentence_queue.task_done()
                break
            
            await audio_queue.put(audio_bytes)
            print(f"[TTS→Audio Queue] Chunk ready ({len(audio_bytes)} bytes)")
            
            sentence_queue.task_done()
            
    except asyncio.CancelledError:
        print("[TTS Consumer] Cancelled.")
    except Exception as e:
        print(f"[TTS Consumer Error] {e}")
        traceback.print_exc()
    # ---
    # !! REMOVED FLAWED FINALLY BLOCK !!
    # The agent_handler_task is responsible for clearing the audio_queue.
    # This task should NOT send a sentinel to the audio_queue,
    # as the audio_sender_task is permanent.
    # ---
    finally:
        print("[TTS Consumer] Task finished.")


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
                # This should no longer be called, but as a safeguard:
                print("[Audio Streamer] Stream complete (via sentinel)")
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


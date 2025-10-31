import json
from datetime import datetime
from typing import AsyncGenerator
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from app.agent.graph import create_agent_graph
from app.agent.state import PatientInfoExtraction
from app.agent.nodes import tool_node, agent_node
from app.agent.prompts import build_system_prompt
from app.agent.tools import update_patient_info, end_call, forward_call_to_human
from app.streaming.pipeline import agent_node_streaming
from app.database import end_call_and_save

class MedicareAgent:
    """
    Wrapper class for agent interaction with streaming and DB buffering.
    """
    def __init__(self):
        self.app = create_agent_graph()
        # In-memory buffer for active calls
        self._call_buffers = {}

    def _get_buffer(self, session_id: str):
        """Get or create an in-memory buffer for a session."""
        if session_id not in self._call_buffers:
            self._call_buffers[session_id] = {
                "turns": [],
                "started_at": datetime.utcnow(),
                "caller_id": None, # Can be set by websocket
                "patient_info": PatientInfoExtraction()
            }
        return self._call_buffers[session_id]

    async def process_message_streaming(
        self,
        session_id: str,
        user_message: str
    ) -> AsyncGenerator[dict, None]:
        """
        Process user message with streaming support.
        Yields sentences as they're generated from the LLM.
        """
        config = {"configurable": {"thread_id": session_id}}
        buffer = self._get_buffer(session_id)
        
        # Add user message to buffer and state
        if user_message:
            buffer["turns"].append({"role": "user", "content": user_message, "timestamp": datetime.utcnow()})
            input_data = {"messages": [HumanMessage(content=user_message)]}
        else:
            input_data = {"messages": []} # For greeting

        # Get current state from LangGraph's checkpointer
        current_state = self.app.get_state(config)
        current_messages = current_state.values.get("messages", [])
        current_patient_info = current_state.values.get("patient_info", PatientInfoExtraction())
        
        # Update buffer with latest info
        buffer["patient_info"] = current_patient_info

        # Manually construct the state for the streaming node
        full_state = {
            "messages": current_messages + input_data["messages"],
            "patient_info": current_patient_info
        }

        full_agent_response = ""
        
        # Stream from agent node
        async for chunk in agent_node_streaming(full_state):
            if chunk.get("type") == "sentence":
                full_agent_response += chunk["sentence"] + " "
                yield {"sentence": chunk["sentence"]}

            elif chunk.get("type") == "final":
                # Now properly update the graph state
                final_message = chunk["messages"][0]  # The AIMessage
                
                # Add agent response to buffer
                if full_agent_response.strip():
                     buffer["turns"].append({"role": "agent", "content": full_agent_response.strip(), "timestamp": datetime.utcnow()})

                # Update LangGraph state
                update_data = {
                    "messages": input_data["messages"] + [final_message]
                }
                self.app.update_state(config, update_data)
                
                # Handle tool calls if any
                if isinstance(final_message, AIMessage) and final_message.tool_calls:
                    tool_state = self.app.get_state(config)
                    tool_result = tool_node(tool_state.values)
                    
                    # Update buffer with new patient info from tool
                    if "patient_info" in tool_result:
                        buffer["patient_info"] = tool_result["patient_info"]

                    # Update LangGraph state
                    self.app.update_state(config, tool_result)
                    updated_state = self.app.get_state(config)
                    
                    # Check if we need to continue to agent
                    next_step = self.app.config["edges"]["tool_node"].get(
                        updated_state.values
                    )
                    
                    # This logic needs simplification, but let's follow original
                    # A bit of a hack: checking the *compiled* graph logic
                    # A better way is to re-run `after_tool`
                    # For now, let's assume `update_patient_info` always continues
                    tool_name = final_message.tool_calls[0]["name"]
                    
                    if tool_name == "update_patient_info":
                        follow_up_state = self.app.get_state(config)
                        follow_up_result = agent_node(follow_up_state.values)
                        
                        self.app.update_state(config, follow_up_result)
                        
                        follow_up_message = follow_up_result["messages"][0]
                        if isinstance(follow_up_message, AIMessage) and follow_up_message.content:
                            buffer["turns"].append({"role": "agent", "content": follow_up_message.content, "timestamp": datetime.utcnow()})
                            yield {"sentence": follow_up_message.content}

                yield {"final": True}
                return

    def end_call(self, session_id: str, reason: str = "completed"):
        """Flush buffer to DB and clear from memory."""
        if session_id in self._call_buffers:
            buffer = self._call_buffers[session_id]
            
            # Get latest patient info from checkpointer just in case
            final_state = self.get_patient_info(session_id)
            if final_state:
                 buffer["patient_info"] = final_state

            end_call_and_save(session_id, buffer, reason)
            del self._call_buffers[session_id]
        print(f"Call {session_id} ended with reason: {reason}")


    def get_patient_info(self, session_id: str) -> dict:
        """Get current patient information from the checkpointer."""
        config = {"configurable": {"thread_id": session_id}}
        state_snapshot = self.app.get_state(config)
        
        if state_snapshot and state_snapshot.values.get("patient_info"):
            return state_snapshot.values["patient_info"].model_dump()
            
        return {}

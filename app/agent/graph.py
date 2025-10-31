from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage
from app.agent.state import InterviewState
from app.agent.nodes import agent_node, tool_node

def should_call_tool(state: InterviewState):
    """Decide if tools should be called"""
    if not state.get("messages"):
        return END # Should not happen, but safeguard
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tool_node"
    else:
        return END

def after_tool(state: InterviewState):
    """Decide next step after tool execution"""
    # state["messages"][-2] is the AIMessage with the tool call
    ai_message = state["messages"][-2] 
    if not isinstance(ai_message, AIMessage) or not ai_message.tool_calls:
         return END # Should not happen

    tool_name = ai_message.tool_calls[0]["name"]
    
    if tool_name == "update_patient_info":
        return "agent_node"
    else:
        return END

def create_agent_graph():
    """Build and compile the agent graph"""
    builder = StateGraph(InterviewState)
    
    builder.add_node("agent_node", agent_node)
    builder.add_node("tool_node", tool_node)
    
    builder.set_entry_point("agent_node")
    
    builder.add_conditional_edges(
        "agent_node",
        should_call_tool,
        {"tool_node": "tool_node", END: END}
    )
    
    builder.add_conditional_edges(
        "tool_node",
        after_tool,
        {"agent_node": "agent_node", END: END}
    )
    
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)

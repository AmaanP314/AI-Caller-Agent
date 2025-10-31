import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, ToolMessage
from app.config import GOOGLE_API_KEY
from app.agent.state import InterviewState, PatientInfoExtraction
from app.agent.tools import update_patient_info, end_call, forward_call_to_human
from app.agent.prompts import build_system_prompt

def agent_node(state: InterviewState):
    """
    Non-streaming version (kept for compatibility with tool execution flow).
    This is used after tool calls to continue the conversation.
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
    
    response = model.invoke([system_prompt] + relevant_history)
    return {"messages": [response]}


def tool_node(state: InterviewState):
    """Execute tools and update state"""
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage):
        return {}
        
    new_info_obj = state.get('patient_info') or PatientInfoExtraction()
    tool_messages = []
    
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        
        if tool_name == "update_patient_info":
            tool_output_json = update_patient_info.invoke(tool_call["args"])
            tool_messages.append(ToolMessage(content=tool_output_json, tool_call_id=tool_call["id"]))
            new_data_dict = json.loads(tool_output_json)
            new_info_obj = new_info_obj.model_copy(update=new_data_dict)
            print(f"[Tool] Updated: {new_data_dict}")
            
        elif tool_name == "end_call":
            tool_output = end_call.invoke(tool_call["args"])
            tool_messages.append(ToolMessage(content=tool_output, tool_call_id=tool_call["id"]))
            print(f"[Tool] Ending call: {tool_call['args']}")
            
        elif tool_name == "forward_call_to_human":
            tool_output = forward_call_to_human.invoke(tool_call["args"])
            tool_messages.append(ToolMessage(content=tool_output, tool_call_id=tool_call["id"]))
            print(f"[Tool] Forwarding: {tool_call['args']}")
    
    return {"messages": tool_messages, "patient_info": new_info_obj}

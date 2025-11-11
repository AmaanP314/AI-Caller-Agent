import json
from typing import Optional, List
from typing_extensions import Annotated
from langchain_core.tools import tool

@tool
def update_patient_info(
    patient_name: Annotated[Optional[str], "Patient's full name"] = None,
    medical_conditions: Annotated[Optional[List[str]], "List of any immune-related or neurological or cancer medical conditions"] = None,
    last_visit_date: Annotated[Optional[str], "When patient last visited their doctor"] = None,
    interested: Annotated[Optional[bool], "Whether patient is interested in moving forward"] = None,
):
    """Update patient information with new data"""
    locals_copy = locals().copy()
    updates = {k: v for k, v in locals_copy.items() if v is not None}
    return json.dumps(updates)


@tool
def end_call(reason: str):
    """End the conversation"""
    return f"Call ended with reason: {reason}"


@tool
def forward_call_to_human(reason: str):
    """Forward call to human agent"""
    return f"Call forwarded to human with reason: {reason}"

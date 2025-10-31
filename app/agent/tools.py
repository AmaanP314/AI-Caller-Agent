import json
from typing import Optional, List
from typing_extensions import Annotated
from langchain_core.tools import tool

@tool
def update_patient_info(
    customer_name: Annotated[Optional[str], "Patient's full name"] = None,
    ethnicity: Annotated[Optional[str], "Patient's ethnic background"] = None,
    height: Annotated[Optional[str], "Patient's height"] = None,
    weight: Annotated[Optional[str], "Patient's weight"] = None,
    immune_conditions: Annotated[Optional[List[str]], "List of immune-related conditions"] = None,
    neuro_conditions: Annotated[Optional[List[str]], "List of neurological conditions"] = None,
    cancer_history: Annotated[Optional[List[str]], "List of any cancer diagnoses"] = None,
    last_visit_date: Annotated[Optional[str], "When patient last visited their doctor"] = None,
    can_make_decisions: Annotated[Optional[bool], "Whether patient is capable of making own medical decisions"] = None,
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

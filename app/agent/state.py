from typing import TypedDict, Annotated, Sequence, Optional, List
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

class PatientInfoExtraction(BaseModel):
    """Patient information schema"""
    patient_name: Optional[str] = Field(None, description="Patient's full name")
    ethnicity: Optional[str] = Field(None, description="Patient's ethnic background")
    height: Optional[str] = Field(None, description="Patient's height")
    weight: Optional[str] = Field(None, description="Patient's weight")
    medical_conditions: Optional[List[str]] = Field(None, description="List of any immune-related or neurological or cancer medical conditions")
    last_visit_date: Optional[str] = Field(None, description="When patient last visited their doctor")
    can_make_decisions: Optional[bool] = Field(None, description="Whether patient is capable of making own medical decisions")
    interested: Optional[bool] = Field(None, description="Whether patient is interested in moving forward")

class InterviewState(TypedDict):
    """Graph state definition"""
    messages: Annotated[Sequence[BaseMessage], lambda x, y: x + y]
    patient_info: PatientInfoExtraction

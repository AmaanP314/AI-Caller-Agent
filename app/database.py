import os
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import DATABASE_URL

# --- SQLAlchemy Setup ---
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# --- Database Model ---
# We take this from your first app.py, as it's crucial for logging.
class Conversation(Base):
    __tablename__ = "conversations"
    
    session_id = Column(String, primary_key=True)
    phone_number = Column(String, index=True, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String, default="running")
    
    # Patient Data
    patient_name = Column(String, nullable=True)
    medical_conditions = Column(String, nullable=True)
    last_visit_date = Column(String, nullable=True)
    interested = Column(Boolean, nullable=True)
    
    # Conversation
    conversation_json = Column(JSON, nullable=True)
    total_turns = Column(Integer, default=0)
    greeting = Column(Text, nullable=True)
    first_user_response = Column(Text, nullable=True)

# Create all tables
def init_db():
    Base.metadata.create_all(engine)
    print("✓ Database initialized")

# --- Database Utility Functions ---
# This buffered-write logic from your first app.py is excellent.
# We will adapt the MedicareAgent to use this.
def end_call_and_save(session_id: str, buffer: dict, reason: str = "completed"):
    """Save conversation buffer to database"""
    db_session = SessionLocal()
    
    try:
        turns = buffer.get("turns", [])
        conversation_structured = []
        
        for i, turn in enumerate(turns):
            conversation_structured.append({
                "turn_number": i + 1,
                "role": turn["role"],
                "content": turn["content"],
                "timestamp": turn.get("timestamp", datetime.utcnow()).isoformat()
            })
        
        greeting = None
        first_user_response = None
        
        for turn in turns:
            if turn["role"] == "agent" and greeting is None:
                greeting = turn["content"]
            elif turn["role"] == "user" and first_user_response is None:
                first_user_response = turn["content"]
        
        patient_info = buffer.get("patient_info")
        patient_data = {}
        
        if patient_info:
            if hasattr(patient_info, 'model_dump'):
                updates = patient_info.model_dump()
            else:
                updates = patient_info # Assume it's already a dict
            patient_data = {k: v for k, v in updates.items() if v is not None}
        
        existing = db_session.query(Conversation).filter_by(session_id=session_id).first()
        
        if existing:
            existing.ended_at = datetime.utcnow()
            existing.status = reason
            existing.conversation_json = conversation_structured
            existing.total_turns = len(turns)
            existing.greeting = greeting
            existing.first_user_response = first_user_response
            for field, value in patient_data.items():
                if hasattr(existing, field):
                    # Handle list-to-string conversion
                    if isinstance(value, list):
                        value = ", ".join(value)
                    setattr(existing, field, value)
        else:
            # Handle list-to-string conversion for new entries
            for field, value in patient_data.items():
                if isinstance(value, list):
                    patient_data[field] = ", ".join(value)
                    
            conversation = Conversation(
                session_id=session_id,
                phone_number=buffer.get("caller_id"),
                started_at=buffer.get("started_at", datetime.utcnow()),
                ended_at=datetime.utcnow(),
                status=reason,
                conversation_json=conversation_structured,
                total_turns=len(turns),
                greeting=greeting,
                first_user_response=first_user_response,
                **patient_data
            )
            db_session.add(conversation)
        
        db_session.commit()
        print(f"✓ [{session_id}] Saved: {len(turns)} turns, status: {reason}")
        
    except Exception as e:
        print(f"✗ [{session_id}] Database error: {e}")
        db_session.rollback()
    finally:
        db_session.close()

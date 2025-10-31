import os
import uvicorn
import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from transformers import pipeline
from transformers.utils import is_flash_attn_2_available
from kokoro import KPipeline

# --- Local Module Imports ---
from app.config import WHISPER_MODEL, KOKORO_LANG, GOOGLE_API_KEY
from app.database import init_db
from app.api import http, websocket
from app.audio import stt, tts
from app.streaming.manager import MedicareAgent
from app.streaming.pipeline import set_agent_manager as set_pipeline_agent_manager

# --- Global Variables ---
app = FastAPI(title="Medicare AI Voice Agent", version="1.1.0-refactored")
agent_manager = MedicareAgent()

# --- Model Loading ---
def load_models():
    """Load and initialize all ML models."""
    print("🚀 Loading models...")
    
    # Configure device
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    
    # Configure Flash Attention
    use_flash_attn = is_flash_attn_2_available()
    model_kwargs = {"attn_implementation": "flash_attention_2"} if use_flash_attn else {"attn_implementation": "sdpa"}
    
    print(f"  Device: {device}")
    print(f"  Attention: {'Flash Attention 2' if use_flash_attn else 'SDPA'}")
    
    # 1. Load Whisper (STT)
    whisper_pipe = pipeline(
        "automatic-speech-recognition",
        model=WHISPER_MODEL,
        torch_dtype=torch_dtype,
        device=device,
        model_kwargs=model_kwargs,
    )
    stt.set_whisper_pipeline(whisper_pipe)
    print(f"✓ Whisper loaded")
    
    # 2. Load Kokoro (TTS)
    try:
        tts_model_instance = KPipeline(lang_code=KOKORO_LANG)
        tts.set_tts_model(tts_model_instance)
        print(f"✓ Kokoro TTS loaded")
    except Exception as e:
        print(f"✗ Failed to load Kokoro TTS: {e}")
        tts.set_tts_model(None) # Set to None so it can fallback
        
    print("✓ Models loaded successfully")

# --- FastAPI App Setup ---
@app.on_event("startup")
async def startup_event():
    """On startup, load models and init DB."""
    if not GOOGLE_API_KEY:
        print("="*50)
        print("ERROR: GOOGLE_API_KEY is not set. The agent will not work.")
        print("Please create a .env file and add your key.")
        print("="*50)
        # In a real prod env, you might want to raise an Exception
    
    load_models()
    init_db()
    
    # Inject dependencies
    http.set_agent_manager(agent_manager)
    set_pipeline_agent_manager(agent_manager)
    # websocket router will get it via Depends()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(http.router)
app.include_router(websocket.router)

# --- Server Startup ---
# if __name__ == "__main__":
#     port = int(os.getenv("PORT", 8000))
#     print(f"\n🚀 Starting Medicare AI Voice Agent on port {port}")
#     print(f"📡 Health check: http://localhost:{port}/")
#     print(f"🔗 Test Endpoint: http://localhost:{port}/docs")
#     print(f"🔗 VICIdial WebSocket: ws://localhost:{port}/ws/vicidial/{{session_id}}\n")
    
#     uvicorn.run(
#         "main:app",
#         host="0.0.0.0",
#         port=port,
#         workers=1,
#         log_level="info",
#         reload=True # Enable reload for development
#     )

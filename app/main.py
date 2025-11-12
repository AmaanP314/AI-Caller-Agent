import os
import uvicorn
import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from transformers import pipeline
from transformers.utils import is_flash_attn_2_available
from kokoro import KPipeline

# --- Local Module Imports ---
from app.config import (
    WHISPER_MODEL,
    KOKORO_LANG,
    KOKORO_VOICE,
    GOOGLE_API_KEY,
    EXECUTOR_MAX_WORKERS,
    ENABLE_AUDIO_LOGGING,
    AUDIO_LOG_DIR,
    VAD_SPEECH_THRESHOLD,
    VAD_SILENCE_TIMEOUT_MS,
    MIN_SPEECH_DURATION_MS,
    MIN_BARGEIN_SPEECH_MS
)
from app.database import init_db
from app.api import http, websocket
from app.audio import stt, tts, vad
from app.streaming.manager import MedicareAgent
from app.streaming.pipeline import set_agent_manager as set_pipeline_agent_manager

# --- Global Variables ---
app = FastAPI(title="Medicare AI Voice Agent", version="2.0.0-config-integrated")
agent_manager = MedicareAgent()

# --- Model Loading ---
def load_models():
    """Load and initialize all ML models."""
    print("🚀 Loading models...")
    print(f"📋 Configuration:")
    print(f"   Whisper model: {WHISPER_MODEL}")
    print(f"   Kokoro voice: {KOKORO_VOICE}")
    print(f"   VAD threshold: {VAD_SPEECH_THRESHOLD}")
    print(f"   Silence timeout: {VAD_SILENCE_TIMEOUT_MS}ms")
    print(f"   Min speech duration: {MIN_SPEECH_DURATION_MS}ms")
    print(f"   Min barge-in: {MIN_BARGEIN_SPEECH_MS}ms")
    
    if ENABLE_AUDIO_LOGGING:
        print(f"   🎙️  Audio logging: ENABLED → {AUDIO_LOG_DIR.absolute()}")
        AUDIO_LOG_DIR.mkdir(exist_ok=True)
    else:
        print(f"   Audio logging: DISABLED")
    
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
    print(f"✅ Whisper loaded: {WHISPER_MODEL}")
    
    # 2. Load Kokoro (TTS)
    try:
        tts_model_instance = KPipeline(lang_code=KOKORO_LANG)
        tts.set_tts_model(tts_model_instance)
        print(f"✅ Kokoro TTS loaded: {KOKORO_VOICE}")
    except Exception as e:
        print(f"❌ Failed to load Kokoro TTS: {e}")
        tts.set_tts_model(None) # Set to None so it can fallback
        
    # 3. Load Silero VAD
    try:
        vad.create_vad_model() # This loads and sets the model internally
    except Exception as e:
        print(f"❌ Failed to load Silero VAD: {e}")
        
    print("✅ All models loaded successfully\n")

# --- FastAPI App Setup ---
@app.on_event("startup")
async def startup_event():
    """On startup, load models and init DB."""
    print("\n" + "="*60)
    print("Medicare AI Voice Agent - Starting Up")
    print("="*60 + "\n")
    
    if not GOOGLE_API_KEY:
        print("="*60)
        print("ERROR: GOOGLE_API_KEY is not set. The agent will not work.")
        print("Please create a .env file and add your key.")
        print("="*60)
    
    load_models()
    init_db()
    
    # Inject dependencies
    http.set_agent_manager(agent_manager)
    set_pipeline_agent_manager(agent_manager)
    
    print("="*60)
    print("✅ Startup complete - Ready to accept calls")
    print("="*60 + "\n")

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

@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "models": {
            "whisper": WHISPER_MODEL,
            "tts_voice": KOKORO_VOICE,
            "vad_threshold": VAD_SPEECH_THRESHOLD
        },
        "config": {
            "silence_timeout_ms": VAD_SILENCE_TIMEOUT_MS,
            "min_speech_duration_ms": MIN_SPEECH_DURATION_MS,
            "min_bargein_ms": MIN_BARGEIN_SPEECH_MS,
            "audio_logging": ENABLE_AUDIO_LOGGING
        }
    }

@app.get("/config")
async def get_config():
    """Get current configuration (useful for debugging)."""
    return {
        "vad": {
            "silence_timeout_ms": VAD_SILENCE_TIMEOUT_MS,
            "speech_threshold": VAD_SPEECH_THRESHOLD,
            "min_speech_duration_ms": MIN_SPEECH_DURATION_MS,
            "min_bargein_ms": MIN_BARGEIN_SPEECH_MS
        },
        "whisper": {
            "model": WHISPER_MODEL
        },
        "tts": {
            "voice": KOKORO_VOICE,
            "lang": KOKORO_LANG
        },
        "debug": {
            "audio_logging": ENABLE_AUDIO_LOGGING,
            "log_dir": str(AUDIO_LOG_DIR.absolute()) if ENABLE_AUDIO_LOGGING else None
        }
    }

# if __name__ == "__main__":
#     uvicorn.run(
#         "app.main:app",
#         host="0.0.0.0",
#         port=8000,
#         reload=False  # Set to True for development
#     )
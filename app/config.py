# import os
# import dotenv

# # Load environment variables from .env file
# dotenv.load_dotenv()

# # --- Google API ---
# GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
# if not GOOGLE_API_KEY:
#     # In a real app, you might raise an error or just warn
#     print("Warning: GOOGLE_API_KEY environment variable not set!")

# # --- Model Config ---
# WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
# KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
# KOKORO_LANG = os.getenv("KOKORO_LANG", "a")

# # --- Database ---
# DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./medicare_agent.db")
# VAD_SPEECH_THRESHOLD = float(os.getenv("VAD_SPEECH_THRESHOLD", 0.7))

# # --- VAD Config (for later steps) ---
# VAD_SENSITIVITY = int(os.getenv("VAD_SENSITIVITY", 3)) # Example
# VAD_SILENCE_TIMEOUT_MS = int(os.getenv("VAD_SILENCE_TIMEOUT_MS", 1000))

"""
Complete configuration file for Medicare AI Voice Agent
All settings in one place for easy tuning
"""
import os
from pathlib import Path

# --- API Keys ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# --- LLM Configuration ---
LLM_MODEL = "gemini-flash-lite-latest"
LLM_TEMPERATURE = 0.7
LLM_MAX_HISTORY = 6  # Keep last N messages in context

# --- VAD Configuration ---
# Balance between responsiveness and false positives

# Silence timeout - how long to wait after speech stops before processing
# Higher = less word cutoffs, but slower responses
VAD_SILENCE_TIMEOUT_MS = 1500  # 1.5 seconds (increased from 1000ms)

# Speech probability threshold (0.0 to 1.0)
# Silero VAD returns confidence - higher = stricter
# Lower = catch quieter speech, but more false positives
# VAD_SPEECH_THRESHOLD = 0.45  # Decreased from 0.5
VAD_SPEECH_THRESHOLD = float(os.getenv("VAD_SPEECH_THRESHOLD", 0.45))

# Minimum speech duration before triggering barge-in
# Prevents accidental mouth noises from interrupting
MIN_BARGEIN_SPEECH_MS = 400  # 400ms (3 consecutive speech chunks)
MIN_BARGEIN_SPEECH_CHUNKS = 3  # Number of consecutive VAD chunks

# Minimum total speech duration before transcription
# Filters out coughs, clicks, short noises
MIN_SPEECH_DURATION_MS = 300  # 300ms minimum

# Energy threshold for audio quality validation
# Audio below this RMS level is considered silence/noise
MIN_AUDIO_ENERGY = 0.001  # 0.001 = very quiet threshold

# Pre-emphasis filter coefficient for noise reduction
# Higher = more high-frequency boost (reduces low-freq noise)
# Range: 0.9-0.97, where 0.95 is balanced
PREEMPHASIS_ALPHA = 0.95

# --- Whisper (STT) Configuration ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-base")  # Best accuracy/speed balance

# Whisper generation parameters for better accuracy
WHISPER_GENERATION_KWARGS = {
    "language": "english",
    "task": "transcribe",
    "temperature": 0.0,
    "no_speech_threshold": 0.6,  # Higher = stricter (reject non-speech)
    "logprob_threshold": -1.0,    # Reject low-confidence transcriptions
    "compression_ratio_threshold": 2.4  # Reject repetitive/garbled output
}

# --- TTS Configuration ---
KOKORO_VOICE = "af_bella"  # Clear, professional voice
KOKORO_LANG = "a"      # English
TTS_SPEED = 1.2  # Slightly faster than normal (1.0 = normal speed)

# --- Audio Quality & Debugging ---
# Enable audio logging for debugging (disable in production!)
ENABLE_AUDIO_LOGGING = False  # Set to True to capture audio for analysis

# Audio logging directory
AUDIO_LOG_DIR = Path("./audio_logs")

# Debug print options
DEBUG_PRINT_AUDIO_STATS = True   # Print energy/duration for each chunk
DEBUG_PRINT_VAD_DECISIONS = False  # Print every VAD decision (very verbose!)

# --- Asterisk/Telephony Configuration ---
# These should match your Asterisk codec settings
ASTERISK_SAMPLE_RATE = 8000
ASTERISK_AUDIO_FORMAT = "slin"  # Signed Linear PCM

# --- Audio Processing ---
# Sample rates used in pipeline
AGENT_SAMPLE_RATE = 16000  # Backend processes at 16kHz
VAD_SAMPLE_RATE = 16000    # Silero VAD expects 16kHz
VAD_CHUNK_SAMPLES = 512    # Silero VAD chunk size for 16kHz

# --- Performance ---
# Thread pool size for blocking operations (STT, TTS)
EXECUTOR_MAX_WORKERS = 4

# --- Network/WebSocket ---
# WebSocket ping interval (keep connection alive)
WS_PING_INTERVAL = 20  # seconds

# --- Timing Tolerances ---
# These help with network jitter and ensure responsive behavior
AUDIO_CHUNK_TIMEOUT = 0.02  # 20ms - matches Asterisk pacing
AUDIO_QUEUE_CHECK_INTERVAL = 0.02  # Check interruption every 20ms
VAD_PROCESSING_TIMEOUT = 0.1  # Max time to process one VAD chunk

# --- Database ---
DATABASE_PATH = "medicare_calls.db"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./medicare_agent.db")

# --- Derived Constants (calculated from above) ---
# Don't modify these directly - they're computed from the settings above
VAD_CHUNK_BYTES = VAD_CHUNK_SAMPLES * 2  # 2 bytes per sample (16-bit PCM)
MS_PER_VAD_CHUNK = (VAD_CHUNK_SAMPLES / VAD_SAMPLE_RATE) * 1000  # ~32ms
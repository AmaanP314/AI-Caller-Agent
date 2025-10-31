import os
import dotenv

# Load environment variables from .env file
dotenv.load_dotenv()

# --- Google API ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
if not GOOGLE_API_KEY:
    # In a real app, you might raise an error or just warn
    print("Warning: GOOGLE_API_KEY environment variable not set!")

# --- Model Config ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-base")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
KOKORO_LANG = os.getenv("KOKORO_LANG", "a")

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./medicare_agent.db")

# --- VAD Config (for later steps) ---
VAD_SENSITIVITY = int(os.getenv("VAD_SENSITIVITY", 3)) # Example
VAD_SILENCE_TIMEOUT_MS = int(os.getenv("VAD_SILENCE_TIMEOUT_MS", 1000))

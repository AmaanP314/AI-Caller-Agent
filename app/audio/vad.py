import torch
import numpy as np
from app.config import (
    VAD_SAMPLE_RATE,
    VAD_CHUNK_SAMPLES,
    VAD_CHUNK_BYTES,
    VAD_SPEECH_THRESHOLD
)

# --- VAD Model Globals ---
vad_model = None
vad_utils = None

def create_vad_model():
    """
    Load the Silero VAD model from torch.hub.
    This will download the model on first run.
    """
    try:
        model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False
        )
        
        # Unpack utils
        (get_speech_timestamps,
         save_audio,
         read_audio,
         VADIterator,
         collect_chunks) = utils
         
        # Store for access
        global vad_model, vad_utils
        vad_model = model
        vad_utils = {
            "get_speech_timestamps": get_speech_timestamps,
            "save_audio": save_audio,
            "read_audio": read_audio,
            "VADIterator": VADIterator,
            "collect_chunks": collect_chunks
        }
        
        print(f"✅ Silero VAD model loaded")
        print(f"   Sample rate: {VAD_SAMPLE_RATE}Hz")
        print(f"   Chunk size: {VAD_CHUNK_SAMPLES} samples ({VAD_CHUNK_BYTES} bytes)")
        print(f"   Speech threshold: {VAD_SPEECH_THRESHOLD}")
        
    except Exception as e:
        print(f"❌ FAILED to load Silero VAD model: {e}")
        print("  Please ensure you have an internet connection for the first run.")
        vad_model = None
        vad_utils = None

def set_vad_model(model, utils):
    """Dependency injection, primarily for consistency."""
    global vad_model, vad_utils
    vad_model = model
    vad_utils = utils

def get_vad_model():
    """Get the loaded VAD model and utils."""
    return vad_model, vad_utils

def is_chunk_speech(pcm_chunk: bytes) -> bool:
    """
    Check if a 16kHz PCM audio chunk contains speech.
    Uses VAD_SPEECH_THRESHOLD from config.
    
    Args:
        pcm_chunk: Bytes of 16-bit 16kHz mono PCM audio.
                   MUST be exactly VAD_CHUNK_BYTES long.
    
    Returns:
        True if speech is detected, False otherwise.
    """
    if vad_model is None:
        print("VAD model not loaded, assuming no speech.")
        return False
        
    if len(pcm_chunk) != VAD_CHUNK_BYTES:
        # Pad or truncate to expected size
        if len(pcm_chunk) < VAD_CHUNK_BYTES:
            padding = bytes(VAD_CHUNK_BYTES - len(pcm_chunk))
            pcm_chunk += padding
        else:
            pcm_chunk = pcm_chunk[:VAD_CHUNK_BYTES]
        
    try:
        # 1. Convert bytes to float32 tensor
        audio_int16 = np.frombuffer(pcm_chunk, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        audio_tensor = torch.from_numpy(audio_float32)
        
        # 2. Get speech probability from model
        speech_prob = vad_model(audio_tensor, VAD_SAMPLE_RATE).item()
        
        # 3. Compare against configured threshold
        return speech_prob > VAD_SPEECH_THRESHOLD
        
    except Exception as e:
        # Catch the specific error about chunk size
        if "Provided number of samples is" in str(e):
            print(f"FATAL VAD ERROR: {e}")
            print(f"Expected {VAD_CHUNK_SAMPLES} samples, got {len(pcm_chunk)//2}")
            print(f"Check VAD_CHUNK_SAMPLES in config.py")
        else:
            print(f"Error during VAD processing: {e}")
        return False
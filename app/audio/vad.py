import torch
import numpy as np

# --- VAD Model Globals ---
vad_model = None
vad_utils = None

# --- VAD Configuration ---
# Silero VAD is optimized for 16kHz PCM chunks
VAD_SAMPLE_RATE = 16000

# The model requires a specific chunk size (in samples)
# The error log told us: 512 for 16000Hz
VAD_CHUNK_SAMPLES = 512 # <-- This was 1536, which was wrong.
 
# 16-bit PCM = 2 bytes per sample
VAD_CHUNK_BYTES = VAD_CHUNK_SAMPLES * 2 # 512 * 2 = 1024 bytes

VAD_SPEECH_THRESHOLD = 0.5 # Confidence threshold

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
        
        print(f"✓ Silero VAD model loaded")
        
    except Exception as e:
        print(f"✗ FAILED to load Silero VAD model: {e}")
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
        print(f"Warning: VAD received incorrect chunk size {len(pcm_chunk)}, expected {VAD_CHUNK_BYTES}")
        # This is common, especially at the end of a stream.
        # Pad with silence if it's smaller.
        if len(pcm_chunk) < VAD_CHUNK_BYTES:
            padding = bytes(VAD_CHUNK_BYTES - len(pcm_chunk))
            pcm_chunk += padding
        else: # If it's larger, truncate
            pcm_chunk = pcm_chunk[:VAD_CHUNK_BYTES]
        
    try:
        # 1. Convert bytes to float32 tensor
        audio_int16 = np.frombuffer(pcm_chunk, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        audio_tensor = torch.from_numpy(audio_float32)
        
        # 2. Get speech probability
        speech_prob = vad_model(audio_tensor, VAD_SAMPLE_RATE).item()
        
        return speech_prob > VAD_SPEECH_THRESHOLD
        
    except Exception as e:
        # Catch the specific error from the log just in case
        if "Provided number of samples is" in str(e):
             print(f"FATAL VAD ERROR: {e}. Check VAD_CHUNK_SAMPLES.")
        else:
            print(f"Error during VAD processing: {e}")
        return False


import io
import audioop
import numpy as np
import scipy.signal
from pydub import AudioSegment

def mulaw_to_pcm16k_bytes(mulaw_bytes: bytes) -> bytes:
    """
    (Batch) Convert 8kHz mulaw audio bytes to 16kHz 16-bit PCM audio bytes.
    - Used by the HTTP endpoint (stt.py)
    """
    pcm_data = audioop.ulaw2lin(mulaw_bytes, 2)
    audio_seg = AudioSegment(
        data=pcm_data,
        sample_width=2,
        frame_rate=8000,
        channels=1
    )
    audio_seg = audio_seg.set_frame_rate(16000)
    return audio_seg.raw_data

def resample_pcm8k_to_pcm16k_scipy(pcm8k_bytes: bytes) -> bytes:
    """
    (Batch) Resample 8kHz PCM to 16kHz PCM using scipy.
    This is high-quality.
    """
    try:
        # 1. Convert pcm8k bytes to float32 numpy array
        audio_int16 = np.frombuffer(pcm8k_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        
        # 2. Resample (8k -> 16k means 2x the samples)
        num_samples = len(audio_float32) * 2
        resampled_float32 = scipy.signal.resample(audio_float32, num_samples)
        
        # 3. Convert back to 16-bit pcm16k bytes
        resampled_float32 = np.clip(resampled_float32, -1.0, 1.0)
        resampled_int16 = (resampled_float32 * 32767).astype(np.int16)
        
        return resampled_int16.tobytes()
    except Exception as e:
        print(f"[UTILS] scipy resampling error: {e}")
        return b'' # Return empty bytes on error
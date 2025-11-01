import io
import audioop
from pydub import AudioSegment

def mulaw_to_pcm16k_bytes(mulaw_bytes: bytes) -> bytes:
    """
    (Batch) Convert 8kHz mulaw audio bytes to 16kHz 16-bit PCM audio bytes.
    - mulaw (from Asterisk) -> 8kHz PCM -> 16kHz PCM (for Whisper)
    """
    # 1. Mulaw to linear PCM (8kHz, 16-bit)
    pcm_data = audioop.ulaw2lin(mulaw_bytes, 2)
    
    # 2. Load into Pydub
    audio_seg = AudioSegment(
        data=pcm_data,
        sample_width=2,
        frame_rate=8000,
        channels=1
    )
    
    # 3. Resample to 16kHz
    audio_seg = audio_seg.set_frame_rate(16000)
    
    return audio_seg.raw_data

def wav_bytes_to_mulaw_bytes(wav_bytes: bytes) -> bytes:
    """
    (Batch) Convert WAV audio bytes (from TTS) to 8kHz mulaw audio bytes.
    - WAV (from TTS) -> 8kHz PCM -> mulaw (for Asterisk)
    """
    # 1. Load WAV bytes into Pydub
    audio_seg = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
    
    # 2. Resample to 8kHz (standard for mulaw)
    audio_seg = audio_seg.set_frame_rate(8000).set_channels(1)
    
    # 3. Convert 16-bit PCM to mulaw
    mulaw_data = audioop.lin2ulaw(audio_seg.raw_data, 2)
    
    return mulaw_data

def convert_mulaw_chunk_to_pcm16k(
    mulaw_chunk: bytes,
    ratecv_state: any
):
    """
    (Streaming) Converts a single 8kHz mulaw chunk to 16kHz PCM.
    Uses audioop.ratecv for efficient streaming resampling.
    
    Args:
        mulaw_chunk: A chunk of mulaw audio bytes.
        ratecv_state: The state object from the previous call. Use None for first call.
        
    Returns:
        A tuple of (pcm_16k_chunk_bytes, new_ratecv_state)
    """
    # 1. Convert 8kHz mulaw to 8kHz 16-bit PCM
    # 2 = 2 bytes (16-bit)
    pcm_8k_chunk = audioop.ulaw2lin(mulaw_chunk, 2)
    
    # 2. Resample 8kHz PCM to 16kHz PCM
    # 2 = 2 bytes (16-bit)
    # 1 = mono
    # 8000 = in rate
    # 16000 = out rate
    # ratecv_state = previous state
    (pcm_16k_chunk, new_state) = audioop.ratecv(
        pcm_8k_chunk, 2, 1, 8000, 16000, ratecv_state
    )
    
    return (pcm_16k_chunk, new_state)


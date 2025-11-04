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

# def convert_mulaw_chunk_to_pcm16k(
#     mulaw_chunk: bytes,
#     ratecv_state: any
# ):
#     """
#     (Streaming) Converts a single 8kHz mulaw chunk to 16kHz PCM.
#     Uses audioop.ratecv for efficient streaming resampling.
    
#     Args:
#         mulaw_chunk: A chunk of mulaw audio bytes.
#         ratecv_state: The state object from the previous call. Use None for first call.
        
#     Returns:
#         A tuple of (pcm_16k_chunk_bytes, new_ratecv_state)
#     """
#     # 1. Convert 8kHz mulaw to 8kHz 16-bit PCM
#     # 2 = 2 bytes (16-bit)
#     pcm_8k_chunk = audioop.ulaw2lin(mulaw_chunk, 2)
    
#     # 2. Resample 8kHz PCM to 16kHz PCM
#     # 2 = 2 bytes (16-bit)
#     # 1 = mono
#     # 8000 = in rate
#     # 16000 = out rate
#     # ratecv_state = previous state
#     (pcm_16k_chunk, new_state) = audioop.ratecv(
#         pcm_8k_chunk, 2, 1, 8000, 16000, ratecv_state
#     )
    
#     return (pcm_16k_chunk, new_state)

def convert_mulaw_chunk_to_pcm16k(mulaw_chunk: bytes, ratecv_state):
    """Convert μ-law 8kHz chunk to PCM 16kHz for VAD"""
    print(f"[UTILS] Input: {len(mulaw_chunk)} bytes μ-law")  # ← ADD
    
    # 1. Convert μ-law to linear PCM (8kHz, 16-bit)
    pcm_8k = audioop.ulaw2lin(mulaw_chunk, 2)
    print(f"[UTILS] After ulaw2lin: {len(pcm_8k)} bytes PCM 8kHz")  # ← ADD
    
    # 2. Resample from 8kHz to 16kHz
    pcm_16k, new_state = audioop.ratecv(
        pcm_8k, 2, 1, 8000, 16000, ratecv_state
    )
    print(f"[UTILS] After ratecv: {len(pcm_16k)} bytes PCM 16kHz")  # ← ADD
    
    return pcm_16k, new_state

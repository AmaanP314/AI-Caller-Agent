import io
import audioop
from pydub import AudioSegment

def mulaw_to_pcm16k_bytes(mulaw_bytes: bytes) -> bytes:
    """
    Convert 8kHz mulaw audio bytes to 16kHz 16-bit PCM audio bytes.
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
    Convert WAV audio bytes (from TTS) to 8kHz mulaw audio bytes.
    - WAV (from TTS) -> 8kHz PCM -> mulaw (for Asterisk)
    """
    # 1. Load WAV bytes into Pydub
    audio_seg = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
    
    # 2. Resample to 8kHz (standard for mulaw)
    audio_seg = audio_seg.set_frame_rate(8000).set_channels(1)
    
    # 3. Convert 16-bit PCM to mulaw
    mulaw_data = audioop.lin2ulaw(audio_seg.raw_data, 2)
    
    return mulaw_data


import io
import numpy as np
import soundfile as sf
import scipy.signal
import audioop
from pydub import AudioSegment
from fastapi import HTTPException

# This will be initialized in main.py and passed
whisper_pipeline = None

def set_whisper_pipeline(pipeline):
    """Inject the loaded Whisper pipeline dependency."""
    global whisper_pipeline
    whisper_pipeline = pipeline

def transcribe_audio(audio_bytes: bytes, source_format: str = "webm") -> str:
    """Transcribe audio to text"""
    if whisper_pipeline is None:
        raise HTTPException(status_code=500, detail="STT model not initialized")
        
    try:
        if source_format == "mulaw":
            pcm_data = audioop.ulaw2lin(audio_bytes, 2)
            audio = AudioSegment(data=pcm_data, sample_width=2, frame_rate=8000, channels=1)
        elif source_format == "pcm16k":
            audio = AudioSegment(data=audio_bytes, sample_width=2, frame_rate=16000, channels=1)
        else: # Assume webm, mp3, etc.
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        
        # Resample to 16kHz, set to mono
        audio = audio.set_frame_rate(16000).set_channels(1)
        
        # Export to WAV buffer for soundfile
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_buffer.seek(0)
        
        # Read with soundfile and convert to float32
        audio_array, sample_rate = sf.read(wav_buffer)
        audio_array = audio_array.astype(np.float32)
        
        # Normalize
        max_val = np.abs(audio_array).max()
        if max_val > 0:
            audio_array = audio_array / max_val
        
        if sample_rate != 16000:
            num_samples = int(len(audio_array) * 16000 / sample_rate)
            audio_array = scipy.signal.resample(audio_array, num_samples)
        
        result = whisper_pipeline(
            audio_array,
            return_timestamps=True,
            generate_kwargs={"language": "english", "task": "transcribe"}
        )
        
        return result["text"].strip()
        
    except Exception as e:
        print(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"STT Error: {str(e)}")

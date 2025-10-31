import io
import inspect
import traceback
import numpy as np
import soundfile as sf
import audioop
import scipy.signal
from pydub import AudioSegment
from app.config import KOKORO_VOICE, KOKORO_LANG

# This will be initialized in main.py and passed
tts_model = None

def set_tts_model(model):
    """Inject the loaded Kokoro model dependency."""
    global tts_model
    tts_model = model

def _get_audio_array_from_tts_result(result):
    """Internal helper to extract audio array from Kokoro's varied outputs."""
    sample_rate = 24000  # Default Kokoro sample rate
    audio_array = None

    if isinstance(result, tuple):
        audio_array, sample_rate = result
    elif isinstance(result, dict):
        audio_array = result.get('audio', result.get('waveform'))
        sample_rate = result.get('sample_rate', 24000)
    elif inspect.isgenerator(result):
        audio_chunks = []
        for chunk_obj in result:
            if chunk_obj is None:
                continue
            
            # Handle KPipeline.Result object
            audio_data = None
            if hasattr(chunk_obj, 'output') and hasattr(chunk_obj.output, 'audio'):
                audio_data = chunk_obj.output.audio
            # Handle raw generator output
            elif hasattr(chunk_obj, 'audio'):
                 audio_data = chunk_obj.audio
            # Handle tuple/dict in generator
            elif isinstance(chunk_obj, (tuple, dict)):
                audio_data, sr_chunk = _get_audio_array_from_tts_result(chunk_obj)
                sample_rate = sr_chunk # Update sample rate from chunk
            else:
                audio_data = chunk_obj

            if audio_data is not None and not isinstance(audio_data, np.ndarray) and hasattr(audio_data, 'numpy'):
                audio_data = audio_data.numpy()
            
            if audio_data is not None and isinstance(audio_data, np.ndarray):
                if audio_data.size > 0:
                    audio_chunks.append(audio_data)
        
        if not audio_chunks:
            audio_array = np.array([], dtype=np.float32)
        else:
            audio_array = np.concatenate(audio_chunks)
    else:
        audio_array = result

    if not isinstance(audio_array, np.ndarray):
        audio_array = np.array(audio_array, dtype=np.float32)
    
    if audio_array.size == 0:
        print("⚠️ TTS Warning: Generated empty audio array. Returning 1s silence.")
        audio_array = np.zeros(sample_rate, dtype=np.float32)
    
    if audio_array.dtype in [np.float32, np.float64]:
        audio_array = np.clip(audio_array, -1.0, 1.0)
        
    return audio_array, sample_rate

def synthesize_speech(text: str, output_format: str = "wav", voice: str = None) -> bytes:
    """
    Synchronous TTS synthesis.
    Converts text to speech and returns bytes in the specified format.
    """
    if tts_model is None:
        raise Exception("TTS model not initialized")
        
    if voice is None:
        voice = KOKORO_VOICE
        
    try:
        result = tts_model(text, voice=voice, speed=1.2)
        audio_array, sample_rate = _get_audio_array_from_tts_result(result)
        
        if output_format == "mulaw":
            pcm_data = (audio_array * 32767).astype(np.int16).tobytes()
            audio_seg = AudioSegment(data=pcm_data, sample_width=2, frame_rate=sample_rate, channels=1)
            audio_seg = audio_seg.set_frame_rate(8000) # Downsample to 8kHz for mulaw
            mulaw_data = audioop.lin2ulaw(audio_seg.raw_data, 2)
            return mulaw_data
            
        elif output_format == "pcm16k":
            if sample_rate != 16000:
                num_samples = int(len(audio_array) * 16000 / sample_rate)
                audio_array = scipy.signal.resample(audio_array, num_samples)
            pcm_data = (audio_array * 32767).astype(np.int16).tobytes()
            return pcm_data
            
        else:  # Default to WAV
            buffer = io.BytesIO()
            sf.write(buffer, audio_array, sample_rate, format='WAV')
            buffer.seek(0)
            return buffer.read()
            
    except Exception as e:
        print(f"❌ TTS Error: {e}")
        traceback.print_exc()
        
        # Fallback: return silence in the requested format
        sample_rate = 8000 if output_format == "mulaw" else (16000 if output_format == "pcm16k" else 24000)
        duration = max(2.0, len(text.split()) * 0.5)
        samples = int(duration * sample_rate)
        audio_array = np.zeros(samples, dtype=np.float32)
        
        if output_format == "mulaw":
            pcm_data = (audio_array * 32767).astype(np.int16).tobytes()
            audio_seg = AudioSegment(data=pcm_data, sample_width=2, frame_rate=sample_rate, channels=1)
            return audioop.lin2ulaw(audio_seg.raw_data, 2)
        elif output_format == "pcm16k":
            return (audio_array * 32767).astype(np.int16).tobytes()
        else:
            buffer = io.BytesIO()
            sf.write(buffer, audio_array, sample_rate, format='WAV')
            buffer.seek(0)
            return buffer.read()

def synthesize_speech_for_pipeline(text: str, output_format: str = "wav", voice: str = None) -> bytes:
    """
    Synchronous TTS synthesis wrapper for the streaming pipeline.
    This is what the tts_consumer calls.
    Returns audio bytes in the requested format.
    """
    return synthesize_speech(text, output_format=output_format, voice=voice)


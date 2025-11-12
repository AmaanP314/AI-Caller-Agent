import io
import inspect
import traceback
import numpy as np
import soundfile as sf
import scipy.signal
from app.config import KOKORO_VOICE, KOKORO_LANG, TTS_SPEED, AGENT_SAMPLE_RATE

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
            
            audio_data = None
            if hasattr(chunk_obj, 'output') and hasattr(chunk_obj.output, 'audio'):
                audio_data = chunk_obj.output.audio
            elif hasattr(chunk_obj, 'audio'):
                audio_data = chunk_obj.audio
            elif isinstance(chunk_obj, (tuple, dict)):
                audio_data, sr_chunk = _get_audio_array_from_tts_result(chunk_obj)
                sample_rate = sr_chunk
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
    Uses KOKORO_VOICE, TTS_SPEED, and AGENT_SAMPLE_RATE from config.
    Generates pcm16k - let relay handle downsampling with stateful resampler.
    """
    if tts_model is None:
        raise Exception("TTS model not initialized")
    
    if voice is None:
        voice = KOKORO_VOICE
    
    try:
        # Use TTS_SPEED from config
        result = tts_model(text, voice=voice, speed=TTS_SPEED)
        audio_array, sample_rate = _get_audio_array_from_tts_result(result)
        
        # Generate pcm16k for relay to downsample
        if output_format == "pcm16k":
            target_rate = AGENT_SAMPLE_RATE  # 16000 from config
            if sample_rate != target_rate:
                # High-quality scipy resampling for ONE-TIME conversion
                num_samples = int(len(audio_array) * target_rate / sample_rate)
                audio_array = scipy.signal.resample(audio_array, num_samples)
                sample_rate = target_rate
            
            audio_array = np.clip(audio_array, -1.0, 1.0)
            
            # Convert to 16-bit PCM
            pcm_data = (audio_array * 32767).astype(np.int16).tobytes()
            
            print(f"[TTS] Generated pcm16k: {len(pcm_data)} bytes @ {AGENT_SAMPLE_RATE}Hz")
            return pcm_data
        
        # Legacy support for pcm8k (for HTTP endpoint if needed)
        elif output_format == "pcm8k":
            target_rate = 8000
            if sample_rate != target_rate:
                num_samples = int(len(audio_array) * target_rate / sample_rate)
                audio_array = scipy.signal.resample(audio_array, num_samples)
            
            pcm_data = (audio_array * 32767).astype(np.int16).tobytes()
            print(f"[TTS] Generated pcm8k: {len(pcm_data)} bytes @ 8000Hz")
            return pcm_data
        
        else:  # Default to WAV
            buffer = io.BytesIO()
            sf.write(buffer, audio_array, sample_rate, format='WAV')
            buffer.seek(0)
            return buffer.read()
    
    except Exception as e:
        print(f"❌ TTS Error: {e}")
        traceback.print_exc()
        
        # Fallback silence
        sample_rate = AGENT_SAMPLE_RATE if output_format == "pcm16k" else (8000 if output_format == "pcm8k" else 24000)
        duration = max(2.0, len(text.split()) * 0.5)
        samples = int(duration * sample_rate)
        audio_array = np.zeros(samples, dtype=np.float32)
        
        if output_format in ["pcm8k", "pcm16k"]:
            return (audio_array * 32767).astype(np.int16).tobytes()
        else:
            buffer = io.BytesIO()
            sf.write(buffer, audio_array, sample_rate, format='WAV')
            buffer.seek(0)
            return buffer.read()

def synthesize_speech_for_pipeline(text: str, output_format: str = "wav", voice: str = None) -> bytes:
    """
    Synchronous TTS synthesis wrapper for the streaming pipeline.
    """
    return synthesize_speech(text, output_format=output_format, voice=voice)
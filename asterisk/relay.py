#!/usr/bin/env python3
"""
AudioSocket Relay - FIXED VERSION
- Stateful resampling (like working version)
- Proper 20ms pacing
- Interruption support
"""
import asyncio
import websockets
import json
import base64
import struct
import audioop
from datetime import datetime

# --- CONFIGURATION ---
LIGHTNING_AI_URL = "wss://8000-dep-01k92g7yv2tx4dsrq54rn6r5ak-d.cloudspaces.litng.ai/ws/vicidial"
HOST = "0.0.0.0"
PORT = 9092

# --- PROTOCOL CONSTANTS ---
TYPE_UUID = 0x01
TYPE_AUDIO_SLIN8K = 0x10
TYPE_HANGUP = 0x00

# --- AUDIO CONSTANTS ---
ASTERISK_CHUNK_MS = 20
ASTERISK_SAMPLE_RATE = 8000
ASTERISK_SAMPLE_WIDTH = 2
ASTERISK_CHUNK_BYTES = 320  # 20ms @ 8kHz

# Agent uses 16kHz
AGENT_SAMPLE_RATE = 16000

class AudioResampler:
    """Stateful resampler - CRITICAL for audio quality"""
    def __init__(self, from_rate, to_rate, width):
        self.from_rate = from_rate
        self.to_rate = to_rate
        self.width = width
        self.state = None  # audioop maintains state here

    def resample(self, chunk: bytes) -> bytes:
        new_chunk, self.state = audioop.ratecv(
            chunk, self.width, 1,
            self.from_rate, self.to_rate,
            self.state
        )
        return new_chunk

async def handle_call(reader, writer):
    session_id = f"call-{int(datetime.now().timestamp())}"
    print(f"[{session_id}] 📞 Connected")

    try:
        uuid_frame = await reader.read(19)
        if not uuid_frame or uuid_frame[0] != TYPE_UUID:
            print(f"[{session_id}] ❌ Invalid UUID frame")
            return
        uuid = uuid_frame[3:].hex()
        print(f"[{session_id}] 🆔 UUID: {uuid}")

        ws_url = f"{LIGHTNING_AI_URL}/{session_id}"
        print(f"[{session_id}] 🔗 Connecting to AI...")

        async with websockets.connect(ws_url, ping_interval=20) as ws:
            print(f"[{session_id}] ✅ Connected to AI")
            
            # Create resamplers for this call
            upsampler = AudioResampler(
                from_rate=ASTERISK_SAMPLE_RATE,
                to_rate=AGENT_SAMPLE_RATE,
                width=ASTERISK_SAMPLE_WIDTH
            )
            
            # Interruption flag shared between tasks
            interruption_flag = {"interrupted": False}
            
            await asyncio.gather(
                forward_asterisk_to_ai(reader, ws, session_id, upsampler),
                forward_ai_to_asterisk(ws, writer, session_id, interruption_flag)
            )

    except Exception as e:
        print(f"[{session_id}] ❌ Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"[{session_id}] 📴 Call ended")

async def forward_asterisk_to_ai(reader, ws, session_id, upsampler):
    """Asterisk (8k) → Upsample (16k) → AI"""
    count = 0
    try:
        while True:
            header = await reader.read(3)
            if not header or len(header) < 3:
                break

            frame_type, length = header[0], struct.unpack('>H', header[1:3])[0]

            if frame_type == TYPE_HANGUP:
                print(f"[{session_id}] ☎️  Hangup from Asterisk")
                await ws.send(json.dumps({"type": "hangup"}))
                break

            if frame_type != TYPE_AUDIO_SLIN8K:
                await reader.read(length)
                continue

            audio_8k = await reader.read(length)
            if not audio_8k:
                break

            # Upsample 8k → 16k (stateful!)
            audio_16k = upsampler.resample(audio_8k)

            count += 1
            await ws.send(json.dumps({
                "type": "audio_data",
                "audio": base64.b64encode(audio_16k).decode(),
                "format": "pcm16k"  # Tell backend it's 16k now
            }))

            if count % 50 == 0:
                print(f"[{session_id}] 📊 {count} packets → AI")

    except Exception as e:
        print(f"[{session_id}] ⚠️  A→AI: {e}")

async def forward_ai_to_asterisk(ws, writer, session_id, interruption_flag):
    """AI → Downsample (8k) → Asterisk with interruption support"""
    
    downsampler = None
    audio_buffer = bytearray()
    
    def write_audio_frame(data_8k):
        frame = struct.pack('B', TYPE_AUDIO_SLIN8K) + struct.pack('>H', len(data_8k)) + data_8k
        writer.write(frame)
    
    try:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            # Handle interruption signal from backend
            if data.get('type') == 'interrupt':
                print(f"[{session_id}] 🛑 INTERRUPT signal received - clearing buffer")
                audio_buffer.clear()
                interruption_flag["interrupted"] = True
                continue

            if data.get('type') == 'audio_response':
                # Reset interruption flag when new audio arrives
                interruption_flag["interrupted"] = False
                
                audio_from_ai = base64.b64decode(data['audio'])
                ai_sample_rate = data.get('sample_rate', 16000)

                # Initialize downsampler on first audio
                if downsampler is None:
                    downsampler = AudioResampler(
                        from_rate=ai_sample_rate,
                        to_rate=ASTERISK_SAMPLE_RATE,
                        width=ASTERISK_SAMPLE_WIDTH
                    )

                # Downsample AI audio to 8k (stateful!)
                audio_8k = downsampler.resample(audio_from_ai)
                
                print(f"[{session_id}] 🔊 Received {len(audio_from_ai)}B @{ai_sample_rate}Hz → {len(audio_8k)}B @8kHz")

                # Add to buffer
                audio_buffer.extend(audio_8k)

                # Send in 20ms chunks with proper pacing
                while len(audio_buffer) >= ASTERISK_CHUNK_BYTES:
                    # Check for interruption before each chunk
                    if interruption_flag["interrupted"]:
                        print(f"[{session_id}] ⏸️  Playback interrupted, clearing remaining buffer")
                        audio_buffer.clear()
                        break
                    
                    chunk = audio_buffer[:ASTERISK_CHUNK_BYTES]
                    audio_buffer = audio_buffer[ASTERISK_CHUNK_BYTES:]
                    
                    write_audio_frame(chunk)
                    await writer.drain()
                    await asyncio.sleep(0.02)  # 20ms pacing - natural speech rhythm

            elif data.get('type') == 'transcript':
                print(f"[{session_id}] 📝 User: {data['text']}")

            elif data.get('type') == 'hangup':
                print(f"[{session_id}] ✋ AI requested hangup")
                break

    except websockets.exceptions.ConnectionClosed:
        print(f"[{session_id}] 🔌 AI WebSocket closed")
    except Exception as e:
        print(f"[{session_id}] ⚠️  AI→A: {e}")

async def main():
    server = await asyncio.start_server(handle_call, HOST, PORT)
    print(f"\n{'='*60}")
    print(f"🚀 AudioSocket Relay FIXED - Stateful Resampling")
    print(f"📍 {HOST}:{PORT} (SLIN@8k ↔ Asterisk)")
    print(f"📡 → {LIGHTNING_AI_URL} (PCM@16k ↔ Agent)")
    print(f"✨ Features: Stateful resampling + Interruption support")
    print(f"{'='*60}\n")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Stopped")
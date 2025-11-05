#!/usr/bin/env python3
"""
AudioSocket Relay - NATIVE PCM (SLIN) TRANSLATOR
Asterisk (slin@8k) <-> Agent (slin@16k)
"""
import asyncio
import websockets
import json
import base64
import struct
from datetime import datetime
import audioop  # For resampling

# --- CONFIGURATION ---
LIGHTNING_AI_URL = "wss://8000-dep-01k92g7yv2tx4dsrq54rn6r5ak-d.cloudspaces.litng.ai/ws/vicidial"
HOST = "0.0.0.0"
PORT = 9092

# --- PROTOCOL CONSTANTS ---
TYPE_UUID = 0x01
TYPE_AUDIO_SLIN8K = 0x10  # This is slin@8k (PCM 16-bit)
TYPE_HANGUP = 0x00

# --- AUDIO CONSTANTS ---
# Asterisk sends 20ms chunks of 8kHz 16-bit PCM
ASTERISK_CHUNK_MS = 20
ASTERISK_SAMPLE_RATE = 8000
ASTERISK_SAMPLE_WIDTH = 2  # 16-bit
ASTERISK_CHUNK_BYTES = int(ASTERISK_SAMPLE_RATE * (ASTERISK_CHUNK_MS / 1000) * ASTERISK_SAMPLE_WIDTH) # 320 bytes

# Agent speaks/listens at 16kHz 16-bit PCM
AGENT_SAMPLE_RATE = 16000
AGENT_SAMPLE_WIDTH = 2 # 16-bit
AGENT_CHUNK_BYTES = int(AGENT_SAMPLE_RATE * (ASTERISK_CHUNK_MS / 1000) * AGENT_SAMPLE_WIDTH) # 640 bytes

class AudioResampler:
    """Stateful resampler using audioop.ratecv"""
    def __init__(self, from_rate, to_rate, width):
        self.from_rate = from_rate
        self.to_rate = to_rate
        self.width = width
        self.state = None # audioop.ratecv state

    def resample(self, chunk: bytes) -> bytes:
        new_chunk, self.state = audioop.ratecv(
            chunk,
            self.width,
            1, # Mono
            self.from_rate,
            self.to_rate,
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

            # Create a stateful resampler for this call
            resampler = AudioResampler(
                from_rate=ASTERISK_SAMPLE_RATE,
                to_rate=AGENT_SAMPLE_RATE,
                width=ASTERISK_SAMPLE_WIDTH
            )

            await asyncio.gather(
                forward_asterisk_to_ai(reader, ws, session_id, resampler),
                forward_ai_to_asterisk(ws, writer, session_id)
            )

    except Exception as e:
        print(f"[{session_id}] ❌ Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"[{session_id}] 📴 Call ended")

async def forward_asterisk_to_ai(reader, ws, session_id, resampler: AudioResampler):
    """Asterisk (slin@8k) → Resample (slin@16k) → AI (slin@16k)"""
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
                print(f"[{session_id}] ⚠️  Ignoring unknown frame type: {hex(frame_type)}")
                await reader.read(length) # Discard payload
                continue

            # 1. Read slin@8k (PCM) audio from Asterisk
            audio_slin_8k = await reader.read(length)
            if not audio_slin_8k:
                break

            # 2. Resample: slin@8k -> slin@16k
            audio_slin_16k = resampler.resample(audio_slin_8k)

            # 3. Send slin@16k to AI
            count += 1
            await ws.send(json.dumps({
                "type": "audio_data",
                "audio": base64.b64encode(audio_slin_16k).decode(),
                "format": "pcm16k" # This is now TRUE
            }))

            if count % 50 == 0:
                print(f"[{session_id}] 📊 {count} packets → AI")

    except Exception as e:
        print(f"[{session_id}] ⚠️  A→AI: {e}")

async def forward_ai_to_asterisk(ws, writer, session_id):
    """AI (slin@8k) → Pass-through → Asterisk (slin@8k)"""

    # Since agent now sends 8kHz directly, no resampling needed
    # But we keep the infrastructure in case we need it later
    downsampler = None

    def write_audio_frame(data_slin_8k):
        """Writes a slin@8k (PCM) audio frame to Asterisk"""
        frame = struct.pack('B', TYPE_AUDIO_SLIN8K) + struct.pack('>H', len(data_slin_8k)) + data_slin_8k
        writer.write(frame)

    try:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            if data.get('type') == 'audio_response':
                # 1. Receive audio from AI (16kHz or 24kHz PCM)
                audio_from_ai = base64.b64decode(data['audio'])

                # Determine AI audio rate from your backend
                ai_sample_rate = data.get('sample_rate', 24000)  # Default to 24kHz if not specified

                # Initialize downsampler on first audio packet
                if downsampler is None:
                    downsampler = AudioResampler(
                        from_rate=ai_sample_rate,
                        to_rate=ASTERISK_SAMPLE_RATE,  # 8000 Hz
                        width=AGENT_SAMPLE_WIDTH  # 16-bit
                    )

                # 2. Downsample AI audio to 8kHz for Asterisk
                audio_slin_8k = downsampler.resample(audio_from_ai)

                print(f"[{session_id}] 🔊 Received {len(audio_from_ai)}B @{ai_sample_rate}Hz → {len(audio_slin_8k)}B @8kHz")

                if 'text' in data:
                    print(f"[{session_id}] 🗣️  {data['text'][:50]}...")

                # 3. Send in 20ms chunks (320 bytes for slin@8k)
                for i in range(0, len(audio_slin_8k), ASTERISK_CHUNK_BYTES):
                    chunk_slin_8k = audio_slin_8k[i:i+ASTERISK_CHUNK_BYTES]
                    if not chunk_slin_8k:
                        break

                    if len(chunk_slin_8k) < ASTERISK_CHUNK_BYTES:
                        chunk_slin_8k += b'\x00' * (ASTERISK_CHUNK_BYTES - len(chunk_slin_8k))

                    # 4. Write slin@8k frame to Asterisk and pace
                    write_audio_frame(chunk_slin_8k)
                    await writer.drain()
                    await asyncio.sleep(0.02)  # 20ms pacing

            elif data.get('type') == 'transcript':
                print(f"[{session_id}] 📝 User: {data['text']}")

            elif data.get('type') == 'hangup':
                print(f"[{session_id}] ✋ AI requested hangup.")
                break

    except websockets.exceptions.ConnectionClosed:
        print(f"[{session_id}] 🔌 AI WebSocket closed")
    except Exception as e:
        print(f"[{session_id}] ⚠️  AI→A: {e}")

async def main():
    server = await asyncio.start_server(handle_call, HOST, PORT)
    print(f"\n{'='*60}")
    print(f"🚀 AudioSocket Relay (Native PCM) Started")
    print(f"📍 {HOST}:{PORT} (Speaking SLIN@8k to Asterisk)")
    print(f"📡 → {LIGHTNING_AI_URL} (Speaking SLIN@16k to Agent)")
    print(f"{'='*60}\n")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Stopped")


#!/usr/bin/env python3
# """
# WORKING AudioSocket Relay - Protocol Compliant
# Based on Asterisk 22 AudioSocket specification
# """
# import asyncio
# import websockets
# import json
# import base64
# import struct
# from datetime import datetime

# LIGHTNING_AI_URL = "wss://8000-dep-01k92g7yv2tx4dsrq54rn6r5ak-d.cloudspaces.litng.ai/ws/vicidial"
# HOST = "0.0.0.0"
# PORT = 9092

# # AudioSocket Protocol Constants
# TYPE_UUID = 0x01      # UUID message
# TYPE_AUDIO = 0x10     # Audio frame
# TYPE_HANGUP = 0x00    # Hangup

# async def handle_call(reader, writer):
#     session_id = f"call-{int(datetime.now().timestamp())}"
#     print(f"[{session_id}] 📞 Connected")

#     try:
#         # 1. READ UUID from Asterisk
#         uuid_frame = await reader.read(19)  # 1 + 2 + 16 bytes
#         if not uuid_frame or uuid_frame[0] != TYPE_UUID:
#             print(f"[{session_id}] ❌ Invalid UUID frame")
#             return

#         uuid = uuid_frame[3:].hex()
#         print(f"[{session_id}] 🆔 UUID: {uuid}")

#         # 2. Connect to AI
#         ws_url = f"{LIGHTNING_AI_URL}/{session_id}"
#         print(f"[{session_id}] 🔗 Connecting to AI...")

#         async with websockets.connect(ws_url, ping_interval=20) as ws:
#             print(f"[{session_id}] ✅ Connected to AI")

#             # 3. Run bidirectional audio
#             await asyncio.gather(
#                 forward_asterisk_to_ai(reader, ws, session_id),
#                 forward_ai_to_asterisk(ws, writer, session_id)
#             )

#     except Exception as e:
#         print(f"[{session_id}] ❌ Error: {e}")
#         import traceback
#         traceback.print_exc()
#     finally:
#         writer.close()
#         await writer.wait_closed()
#         print(f"[{session_id}] 📴 Call ended")

# async def forward_asterisk_to_ai(reader, ws, session_id):
#     """Asterisk → AI"""
#     count = 0
#     try:
#         while True:
#             # Read frame header (3 bytes)
#             header = await reader.read(3)
#             if not header or len(header) < 3:
#                 break

#             frame_type, length = header[0], struct.unpack('>H', header[1:3])[0]

#             if frame_type == TYPE_HANGUP:
#                 print(f"[{session_id}] ☎️ Hangup")
#                 break
#             elif frame_type == TYPE_AUDIO:
#                 audio = await reader.read(length)
#                 count += 1
#                 if count % 50 == 0:
#                     print(f"[{session_id}] 📊 {count} packets → AI")

#                 await ws.send(json.dumps({
#                     "type": "audio_data",
#                     "audio": base64.b64encode(audio).decode(),
#                     "format": "mulaw"
#                 }))
#     except Exception as e:
#         print(f"[{session_id}] ⚠️ A→AI: {e}")

# async def forward_ai_to_asterisk(ws, writer, session_id):
#     """AI → Asterisk"""
#     silence = b'\xff' * 320
#     last_time = asyncio.get_event_loop().time()

#     def write_audio_frame(data):
#         """Write AudioSocket audio frame"""
#         frame = struct.pack('B', TYPE_AUDIO) + struct.pack('>H', len(data)) + data
#         writer.write(frame)

#     try:
#         while True:
#             try:
#                 msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
#                 data = json.loads(msg)

#                 if data.get('type') == 'audio_response':
#                     audio = base64.b64decode(data['audio'])

#                     # Send in chunks
#                     for i in range(0, len(audio), 320):
#                         chunk = audio[i:i+320]
#                         if len(chunk) < 320:
#                             chunk += b'\xff' * (320 - len(chunk))
#                         write_audio_frame(chunk)

#                     await writer.drain()
#                     await asyncio.sleep(1)
#                     last_time = asyncio.get_event_loop().time()
#                     print(f"[{session_id}] 🔊 {len(audio)} bytes → Asterisk")

#                     if 'text' in data:
#                         print(f"[{session_id}] 🗣️ {data['text'][:50]}...")

#                 elif data.get('type') == 'transcript':
#                     print(f"[{session_id}] 📝 User: {data['text']}")

#                 elif data.get('type') == 'hangup':
#                     break

#             except asyncio.TimeoutError:
#                 # Keepalive
#                 if asyncio.get_event_loop().time() - last_time > 0.5:
#                     write_audio_frame(silence)
#                     await writer.drain()
#                     last_time = asyncio.get_event_loop().time()

#     except Exception as e:
#         print(f"[{session_id}] ⚠️ AI→A: {e}")

# async def main():
#     server = await asyncio.start_server(handle_call, HOST, PORT)
#     print(f"\n{'='*60}")
#     print(f"🚀 AudioSocket Relay Started")
#     print(f"📍 {HOST}:{PORT}")
#     print(f"📡 → {LIGHTNING_AI_URL}")
#     print(f"{'='*60}\n")

#     async with server:
#         await server.serve_forever()

# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         print("\n⚠️ Stopped")
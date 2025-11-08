#!/usr/bin/env python3
"""
AudioSocket Relay - NATIVE PCM (SLIN) "DUMB" PROXY
Asterisk (slin@8k) <-> Agent (pcm8k)
WITH 100ms BUFFER-AND-BURST PACING
"""
import asyncio
import websockets
import json
import base64
import struct
from datetime import datetime

# --- CONFIGURATION ---
LIGHTNING_AI_URL = "wss://8000-dep-01k92g7yv2tx4dsrq54rn6r5ak-d.cloudspaces.litng.ai/ws/vicidial"
HOST = "0.0.0.0"
PORT = 9092

# --- PROTOCOL CONSTANTS ---
TYPE_UUID = 0x01
TYPE_AUDIO_SLIN8K = 0x10  # This is slin@8k (PCM 16-bit)
TYPE_HANGUP = 0x00

# --- AUDIO CONSTANTS ---
ASTERISK_CHUNK_MS = 20
ASTERISK_SAMPLE_RATE = 8000
ASTERISK_SAMPLE_WIDTH = 2  # 16-bit
ASTERISK_CHUNK_BYTES = 320 # 20ms of slin@8k (8000 * 0.02 * 2)

# --- PACING FIX ---
# We will buffer 100ms of audio and send it in one burst
PACING_BUFFER_MS = 100
CHUNKS_PER_BURST = int(PACING_BUFFER_MS / ASTERISK_CHUNK_MS) # 100ms / 20ms = 5 chunks

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
            
            await asyncio.gather(
                forward_asterisk_to_ai(reader, ws, session_id),
                forward_ai_to_asterisk(ws, writer, session_id)
            )

    except Exception as e:
        print(f"[{session_id}] ❌ Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"[{session_id}] 📴 Call ended")

async def forward_asterisk_to_ai(reader, ws, session_id):
    """Asterisk (slin@8k) → Pass-through → AI (pcm8k)"""
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

            audio_slin_8k = await reader.read(length)
            if not audio_slin_8k:
                break

            count += 1
            await ws.send(json.dumps({
                "type": "audio_data",
                "audio": base64.b64encode(audio_slin_8k).decode(),
                "format": "pcm8k" # Tell the agent it's 8kHz PCM
            }))

            if count % 50 == 0:
                print(f"[{session_id}] 📊 {count} packets → AI")

    except Exception as e:
        print(f"[{session_id}] ⚠️  A→AI: {e}")

async def forward_ai_to_asterisk(ws, writer, session_id):
    """AI (pcm8k) → Pass-through (paced) → Asterisk (slin@8k)"""

    def write_audio_frame(data_slin_8k):
        """Writes a slin@8k (PCM) audio frame to Asterisk"""
        frame = struct.pack('B', TYPE_AUDIO_SLIN8K) + struct.pack('>H', len(data_slin_8k)) + data_slin_8k
        writer.write(frame)

    audio_buffer = bytearray()
    
    try:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)

            if data.get('type') == 'audio_response':
                audio_slin_8k = base64.b64decode(data['audio'])
                print(f"[{session_id}] 🔊 Received {len(audio_slin_8k)} bytes (pcm8k) from AI")
                if 'text' in data:
                    print(f"[{session_id}] 🗣️  {data['text'][:50]}...")
                
                # Add received audio to our buffer
                audio_buffer.extend(audio_slin_8k)

                # --- PACING FIX ---
                # Calculate total chunks needed for 100ms
                total_bytes_for_burst = ASTERISK_CHUNK_BYTES * CHUNKS_PER_BURST # 320 * 5 = 1600 bytes
                
                # Send audio in 100ms (1600 byte) bursts
                while len(audio_buffer) >= total_bytes_for_burst:
                    # 1. Get the 100ms burst
                    burst_data = audio_buffer[:total_bytes_for_burst]
                    audio_buffer = audio_buffer[total_bytes_for_burst:]
                    
                    # 2. Send all chunks for this burst
                    for i in range(0, len(burst_data), ASTERISK_CHUNK_BYTES):
                        chunk = burst_data[i:i+ASTERISK_CHUNK_BYTES]
                        if len(chunk) == ASTERISK_CHUNK_BYTES:
                            write_audio_frame(chunk)
                    
                    # 3. Drain and sleep for 100ms
                    await writer.drain()
                    await asyncio.sleep(PACING_BUFFER_MS / 1000.0) # 0.1s
                # --- END PACING FIX ---

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
    print(f"🚀 AudioSocket Relay (Native PCM Proxy) Started")
    print(f"📍 {HOST}:{PORT} (Speaking SLIN@8k <-> SLIN@8k)")
    print(f"📡 → {LIGHTNING_AI_URL} (Speaking PCM8k <-> PCM16k)")
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
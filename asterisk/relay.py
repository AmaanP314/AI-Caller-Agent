#!/usr/bin/env python3
"""
WORKING AudioSocket Relay - Protocol Compliant
Based on Asterisk 22 AudioSocket specification
"""
import asyncio
import websockets
import json
import base64
import struct
from datetime import datetime

# --- CONFIGURATION ---
# Point this to your Docker container's public URL/IP
LIGHTNING_AI_URL = "wss://8000-dep-01k92g7yv2tx4dsrq54rn6r5ak-d.cloudspaces.litng.ai/ws/vicidial"
HOST = "0.0.0.0" # Listen on all interfaces
PORT = 9092

# AudioSocket Protocol Constants
TYPE_UUID = 0x01      # UUID message
TYPE_AUDIO = 0x10     # Audio frame
TYPE_HANGUP = 0x00    # Hangup

# Audio Pacing Constants
CHUNK_MS = 20  # Standard telephony packet size (20ms)
CHUNK_SAMPLES = int(8000 * (CHUNK_MS / 1000)) # 8000Hz * 0.020s = 160 samples
CHUNK_BYTES = CHUNK_SAMPLES # 160 samples = 160 bytes for mulaw
SILENCE_CHUNK = b'\xff' * CHUNK_BYTES


async def handle_call(reader, writer):
    session_id = f"call-{int(datetime.now().timestamp())}"
    print(f"[{session_id}] 📞 Connected")

    try:
        # 1. READ UUID from Asterisk
        uuid_frame = await reader.read(19)  # 1 + 2 + 16 bytes
        if not uuid_frame or uuid_frame[0] != TYPE_UUID:
            print(f"[{session_id}] ❌ Invalid UUID frame")
            return

        uuid = uuid_frame[3:].hex()
        print(f"[{session_id}] 🆔 UUID: {uuid}")

        # 2. Connect to AI
        ws_url = f"{LIGHTNING_AI_URL}/{session_id}"
        print(f"[{session_id}] 🔗 Connecting to AI...")

        async with websockets.connect(ws_url, ping_interval=20) as ws:
            print(f"[{session_id}] ✅ Connected to AI")

            # 3. Run bidirectional audio
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
    """Asterisk → AI"""
    count = 0
    try:
        while True:
            # Read frame header (3 bytes)
            header = await reader.read(3)
            if not header or len(header) < 3:
                break

            frame_type, length = header[0], struct.unpack('>H', header[1:3])[0]

            if frame_type == TYPE_HANGUP:
                print(f"[{session_id}] ☎️ Hangup from Asterisk")
                await ws.send(json.dumps({"type": "hangup"}))
                break
            elif frame_type == TYPE_AUDIO:
                audio = await reader.read(length)
                count += 1
                if count % 50 == 0:
                    print(f"[{session_id}] 📊 {count} packets → AI")

                await ws.send(json.dumps({
                    "type": "audio_data",
                    "audio": base64.b64encode(audio).decode(),
                    "format": "mulaw" # This is now true (thanks to extensions.conf)
                }))
    except Exception as e:
        print(f"[{session_id}] ⚠️ A→AI: {e}")

async def forward_ai_to_asterisk(ws, writer, session_id):
    """AI → Asterisk (with 20ms pacing)"""
    
    def write_audio_frame(data):
        """Write AudioSocket audio frame"""
        frame = struct.pack('B', TYPE_AUDIO) + struct.pack('>H', len(data)) + data
        writer.write(frame)

    try:
        while True:
            # 1. Wait for a message from the AI
            msg = await ws.recv()
            data = json.loads(msg)

            if data.get('type') == 'audio_response':
                audio = base64.b64decode(data['audio'])
                print(f"[{session_id}] 🔊 {len(audio)} bytes → Asterisk")
                
                if 'text' in data:
                    print(f"[{session_id}] 🗣️ {data['text'][:50]}...")
                
                # --- THIS IS THE FIX ---
                # 2. Send in 20ms (160-byte) chunks
                for i in range(0, len(audio), CHUNK_BYTES):
                    chunk = audio[i:i+CHUNK_BYTES]
                    if not chunk:
                        break
                        
                    # 3. Pad the final chunk if it's too small
                    if len(chunk) < CHUNK_BYTES:
                        chunk += b'\xff' * (CHUNK_BYTES - len(chunk))
                    
                    write_audio_frame(chunk)
                    
                    # 4. Drain the buffer and sleep for 20ms
                    #    This paces the audio to Asterisk in real-time.
                    await writer.drain()
                    await asyncio.sleep(0.02) # 20ms

            elif data.get('type') == 'transcript':
                print(f"[{session_id}] 📝 User: {data['text']}")

            elif data.get('type') == 'hangup':
                print(f"[{session_id}] ✋ AI requested hangup.")
                break

    except websockets.exceptions.ConnectionClosed:
        print(f"[{session_id}] 🔌 AI WebSocket closed")
    except Exception as e:
        print(f"[{session_id}] ⚠️ AI→A: {e}")

async def main():
    server = await asyncio.start_server(handle_call, HOST, PORT)
    print(f"\n{'='*60}")
    print(f"🚀 AudioSocket Relay Started")
    print(f"📍 {HOST}:{PORT}")
    print(f"📡 → {LIGHTNING_AI_URL}")
    print(f"{'='*60}\n")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️ Stopped")


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
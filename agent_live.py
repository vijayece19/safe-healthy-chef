"""
agent_live.py — Gemini Live Agent with Live Video Display
==========================================================
COMPLETE FIX — No PyAudio at all. Pure PipeWire via subprocesses.

  MIC INPUT  : parec subprocess  → reads 16000 Hz PCM from PipeWire source
  AUDIO OUTPUT: paplay subprocess → writes 24000 Hz PCM to PipeWire sink

  Why: PyAudio talks directly to ALSA. PipeWire intercepts ALSA calls and
  rejects sample rates it doesn't like → "Invalid sample rate" errors.
  parec/paplay talk natively to PipeWire → no ALSA, no rate errors.

  BONUS: parec reads from the headset MIC source directly.
         paplay writes to the headset SINK → .monitor → wf-recorder captures it.

Hardware : Camera Module 3 + USB Headset
Mic source: alsa_input.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device-00.mono-fallback
Sink      : alsa_output.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device-00.analog-stereo
Monitor   : alsa_output.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device-00.analog-stereo.monitor

wf-recorder (Terminal 1 FIRST — video only, audio captured separately):
  wf-recorder -f ~/chef_project/demo_video.mkv

parec for recording AI voice (Terminal 2):
  parec -d alsa_output.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device-00.analog-stereo.monitor \
    --format=s16le --rate=48000 --channels=2 > ~/chef_project/audio_capture.raw

agent (Terminal 3):
  cd ~/chef_project && python3 agent_live.py

After recording merge:
  ffmpeg -f s16le -ar 48000 -ac 2 -i ~/chef_project/audio_capture.raw \
    -i ~/chef_project/demo_video.mkv -c:v copy -c:a aac ~/chef_project/demo_final.mp4
"""

import asyncio
import io
import json
import os
import re
import subprocess
import shutil
import sys
import time
import threading

import cv2
import numpy as np
from PIL import Image
from picamera2 import Picamera2

from google import genai
from google.genai import types

from config import (
    kitchen_state, state_lock, stop_flag,
    GEMINI_API_KEY, ROTATE_180, GEMINI_MODEL
)

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
LIVE_MODEL         = "gemini-2.5-flash-native-audio-latest"
VOICE_NAME         = "Kore"
FRAME_INTERVAL     = 4.0
SCAN_INTERVAL      = 30.0
GEMINI_OUT_RATE    = 24000   # Gemini native audio output: always 24000 Hz
AUDIO_CHUNK        = 4096    # bytes per mic read chunk
DISPLAY_RESOLUTION = (1280, 720)
RECONNECT_DELAY    = 5

# ── USB Headset PipeWire names ────────────────────────────────────────────────
# Run: pactl list short sources   → find your mic source name
# Run: pactl list short sinks     → find your speaker sink name
HEADSET_SOURCE = (
    "alsa_input.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device"
    "-00.mono-fallback"
)
HEADSET_SINK = (
    "alsa_output.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device"
    "-00.analog-stereo"
)
MONITOR_SOURCE = HEADSET_SINK + ".monitor"

# parec captures mic at this rate — must match what we send to Gemini
PAREC_RATE     = 16000
PAREC_CHANNELS = 1


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED LIVE STATE
# ─────────────────────────────────────────────────────────────────────────────
live_state = {
    "connected":     False,
    "ai_speaking":   False,
    "last_response": "Chef AI is initialising ...",
    "reconnecting":  False,
}
live_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIO PLAYER — paplay via PipeWire
#  Forces output to HEADSET_SINK so .monitor → wf-recorder captures it.
# ─────────────────────────────────────────────────────────────────────────────
class AudioPlayer:

    def play(self, data: bytes):
        try:
            with live_lock:
                live_state["ai_speaking"] = True

            proc = subprocess.Popen(
                [
                    "paplay",
                    "--raw",
                    f"--rate={GEMINI_OUT_RATE}",
                    "--format=s16le",
                    "--channels=1",
                    "--volume=65536",
                    "-d", HEADSET_SINK,
                ],
                stdin  = subprocess.PIPE,
                stdout = subprocess.DEVNULL,
                stderr = subprocess.DEVNULL,
            )
            proc.stdin.write(data)
            proc.stdin.close()
            proc.wait()

        except FileNotFoundError:
            print("[Audio] ❌ paplay not found: sudo apt install pulseaudio-utils")
        except Exception as e:
            print(f"[Audio] Play error: {e}")
        finally:
            with live_lock:
                live_state["ai_speaking"] = False

    def close(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  MICROPHONE READER — parec via PipeWire (NO PyAudio)
#
#  parec reads directly from PipeWire source at 16000 Hz mono.
#  No ALSA, no sample rate errors. Chunks are read from stdout pipe.
# ─────────────────────────────────────────────────────────────────────────────
class MicrophoneReader:

    def __init__(self):
        self.proc = None
        self._start()

    def _start(self):
        """Launch parec subprocess reading from the headset mic source."""
        try:
            self.proc = subprocess.Popen(
                [
                    "parec",
                    "-d", HEADSET_SOURCE,
                    "--format=s16le",
                    f"--rate={PAREC_RATE}",
                    f"--channels={PAREC_CHANNELS}",
                    "--latency-msec=50",
                ],
                stdout = subprocess.PIPE,
                stderr = subprocess.DEVNULL,
            )
            print(f"[Mic] parec started ✅  source: {HEADSET_SOURCE}  {PAREC_RATE} Hz")
        except FileNotFoundError:
            raise RuntimeError(
                "[Mic] ❌ parec not found. Run: sudo apt install pulseaudio-utils"
            )
        except Exception as e:
            raise RuntimeError(f"[Mic] ❌ Cannot start parec: {e}")

    def read_chunk(self) -> bytes:
        """Read one chunk of raw PCM from parec stdout."""
        data = self.proc.stdout.read(AUDIO_CHUNK)
        if not data:
            raise RuntimeError("[Mic] parec stream ended")
        return data

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are "Chef AI", a friendly real-time kitchen safety assistant
for the Safe & Healthy Chef system on a Raspberry Pi 5.

You can SEE the kitchen through a live camera and HEAR the chef speak.

Your responsibilities:
1. SAFETY: Alert if pan is handled without gloves.
2. INGREDIENTS: Monitor salt, chilli, oil in the bowl.
   This is a No Oil No Boil Tomato and Carrot Salad.
3. GAS: Alert immediately if told gas is detected.
4. CONVERSATION: Answer chef questions naturally.

Rules:
- Be concise — one or two sentences maximum.
- Be proactive — flag issues immediately when you see them.
- Be friendly — like a helpful sous-chef.
- If salt looks more than a teaspoon, warn immediately.
- If oil visible, warn this violates the No Oil rule.
- If pan present and no gloves visible on hands, warn immediately.
- Always stay listening after every response for follow-up questions.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
#  VISION SCAN
# ─────────────────────────────────────────────────────────────────────────────
VISION_PROMPT = """
Analyse this kitchen image and return ONLY valid JSON, no markdown:
{
  "pan_present":    <true|false>,
  "hands_present":  <true|false>,
  "gloves_on":      <true|false|null>,
  "safety_alert":   <true|false>,
  "salt_level":     <"none"|"pinch"|"teaspoon"|"too_much">,
  "chilli_level":   <"none"|"light"|"moderate"|"too_much">,
  "oil_visible":    <true|false>,
  "vegetables":     ["list"],
  "overall_status": <"healthy"|"warning"|"danger">,
  "notes":          "<one sentence>"
}
Rules:
- pan_present=true if ANY cooking pan/wok/skillet visible
- safety_alert=true ONLY if pan_present AND hands_present AND gloves_on=false
- gloves_on=null if no hands visible
""".strip()


def run_vision_scan(picam2: Picamera2):
    try:
        frame   = picam2.capture_array()
        pil_img = Image.fromarray(frame)
        if ROTATE_180:
            pil_img = pil_img.rotate(180)
        buf = io.BytesIO()
        pil_img.convert("RGB").save(buf, format="JPEG", quality=85)
        image_part = types.Part.from_bytes(
            data=buf.getvalue(), mime_type="image/jpeg"
        )
        scan_client = genai.Client(api_key=GEMINI_API_KEY)
        response    = scan_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=[
                types.Part.from_text(text=VISION_PROMPT),
                image_part
            ])]
        )
        raw    = response.text.strip()
        raw    = re.sub(r"```(?:json)?", "", raw).strip()
        result = json.loads(raw)
        ts     = time.strftime("%H:%M:%S")
        result["safety_time"]     = ts
        result["ingredient_time"] = ts
        with state_lock:
            kitchen_state.update(result)
        print(f"[{ts}] [Vision] Pan:{result.get('pan_present')} "
              f"Status:{result.get('overall_status')}")
    except Exception as e:
        print(f"[Vision Scan] Error: {e}")


def vision_scan_loop(picam2: Picamera2):
    time.sleep(5)
    while not stop_flag.is_set():
        run_vision_scan(picam2)
        time.sleep(SCAN_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
#  FRAME CAPTURE
# ─────────────────────────────────────────────────────────────────────────────
def capture_jpeg(picam2: Picamera2) -> bytes:
    frame   = picam2.capture_array()
    pil_img = Image.fromarray(frame)
    if ROTATE_180:
        pil_img = pil_img.rotate(180)
    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=60)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  OVERLAY DRAWING
# ─────────────────────────────────────────────────────────────────────────────
def draw_overlay(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    with state_lock:
        s = kitchen_state.copy()
    with live_lock:
        ls = live_state.copy()

    BLACK  = (0, 0, 0)
    GREEN  = (0, 200, 0)
    RED    = (0, 0, 220)
    ORANGE = (0, 140, 255)
    GREY   = (120, 120, 120)
    WHITE  = (255, 255, 255)
    YELLOW = (0, 220, 220)
    CYAN   = (255, 220, 0)
    PURPLE = (220, 100, 220)

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 165), BLACK, -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    cv2.circle(frame, (w - 28, 22), 9, RED, -1)
    cv2.putText(frame, "LIVE", (w - 70, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, RED, 2)

    if ls["reconnecting"]:
        ai_color, ai_label = ORANGE, "RECONNECTING"
    elif ls["ai_speaking"]:
        ai_color, ai_label = PURPLE, "SPEAKING"
    elif ls["connected"]:
        ai_color, ai_label = CYAN, "LISTENING"
    else:
        ai_color, ai_label = GREY, "OFFLINE"

    cv2.circle(frame, (w - 28, 55), 7, ai_color, -1)
    cv2.putText(frame, f"AI:{ai_label}", (w - 165, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, ai_color, 1)

    pan_val = s.get('pan_present', False)
    cv2.putText(frame, "SAFETY:", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, f"PAN: {'YES' if pan_val else 'NO'}", (110, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, GREEN if pan_val else GREY, 2)
    hands_val = s.get('hands_present', False)
    cv2.putText(frame, f"HANDS: {'YES' if hands_val else 'NO'}", (270, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, GREEN if hands_val else GREY, 2)
    gloves_val = s.get('gloves_on', None)
    glove_col  = GREEN if gloves_val else RED if gloves_val is False else GREY
    cv2.putText(frame, f"GLOVES: {str(gloves_val).upper()}", (480, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, glove_col, 2)
    cv2.putText(frame, f"[{s.get('safety_time','--:--:--')}]",
                (w - 160, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    cv2.putText(frame, "INGREDIENTS:", (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    salt_val   = s.get('salt_level',   'unknown')
    chilli_val = s.get('chilli_level', 'unknown')
    oil_val    = s.get('oil_visible',  False)
    salt_col   = RED if salt_val   == "too_much" else GREEN if salt_val   != "unknown" else GREY
    chilli_col = RED if chilli_val == "too_much" else GREEN if chilli_val != "unknown" else GREY
    cv2.putText(frame, f"SALT: {salt_val.upper()}", (200, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, salt_col, 2)
    cv2.putText(frame, f"CHILLI: {chilli_val.upper()}", (480, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, chilli_col, 2)
    cv2.putText(frame, f"OIL: {'YES' if oil_val else 'NO'}", (760, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, RED if oil_val else GREEN, 2)
    cv2.putText(frame, f"[{s.get('ingredient_time','--:--:--')}]",
                (w - 160, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    gas_val  = s.get('gas_detected', False)
    temp_val = s.get('temperature', None)
    hum_val  = s.get('humidity', None)
    cv2.putText(frame, "GAS:", (10, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, "DETECTED!" if gas_val else "CLEAR", (75, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, RED if gas_val else GREEN, 2)
    temp_str = f"{temp_val}C" if temp_val is not None else "--"
    hum_str  = f"{hum_val}%" if hum_val  is not None else "--"
    temp_col = (RED if temp_val is not None and temp_val > 35
                else GREEN if temp_val is not None else GREY)
    cv2.putText(frame, f"TEMP:{temp_str} HUM:{hum_str}", (280, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.60, temp_col, 2)
    cv2.putText(frame, f"Chef AI: {ls['last_response'][:72]}", (10, 133),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, CYAN, 1)
    cv2.putText(frame,
                "Speak: 'What do you see?' | 'Is it safe?' | 'Check the salt'",
                (10, 158), cv2.FONT_HERSHEY_SIMPLEX, 0.44, GREY, 1)

    banner_y = h
    if gas_val:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 0, 180), -1)
        cv2.putText(frame, "  GAS DETECTED — CHECK KITCHEN IMMEDIATELY!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, WHITE, 2)
    if s.get('safety_alert', False):
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 60, 180), -1)
        cv2.putText(frame, "  SAFETY: Pan detected — Please wear your gloves!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)
    if oil_val:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 100, 200), -1)
        cv2.putText(frame, "  OIL DETECTED — This is a No Oil recipe!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)
    if salt_val == "too_much":
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (30, 100, 180), -1)
        cv2.putText(frame, "  TOO MUCH SALT — Reduce for a healthier salad!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.78, WHITE, 2)
    if ls["ai_speaking"]:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (80, 20, 80), -1)
        cv2.putText(frame, "  Chef AI is speaking ...",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, WHITE, 2)

    return frame


# ─────────────────────────────────────────────────────────────────────────────
#  ONE LIVE SESSION
# ─────────────────────────────────────────────────────────────────────────────
async def one_session(picam2: Picamera2, client: genai.Client):
    player = AudioPlayer()
    mic    = MicrophoneReader()

    live_config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=VOICE_NAME
                )
            )
        )
    )

    async with client.aio.live.connect(
        model=LIVE_MODEL,
        config=live_config
    ) as session:

        with live_lock:
            live_state["connected"]     = True
            live_state["reconnecting"]  = False
            live_state["last_response"] = "Chef AI is listening — speak freely!"
        print("[Live Agent] ✅ Connected — speak freely!\n")

        async def send_frames():
            last_time = 0
            while not stop_flag.is_set():
                now = time.time()
                if now - last_time >= FRAME_INTERVAL:
                    try:
                        jpeg = capture_jpeg(picam2)
                        await session.send_realtime_input(
                            media=types.Blob(data=jpeg, mime_type="image/jpeg")
                        )
                        last_time = now
                    except Exception as e:
                        err = str(e).lower()
                        if any(k in err for k in (
                            "closed", "websocket", "connection", "eof"
                        )):
                            return
                await asyncio.sleep(0.2)

        async def send_audio():
            loop = asyncio.get_event_loop()
            while not stop_flag.is_set():
                try:
                    with live_lock:
                        speaking = live_state["ai_speaking"]
                    if speaking:
                        await asyncio.sleep(0.1)
                        continue
                    chunk = await loop.run_in_executor(None, mic.read_chunk)
                    await session.send_realtime_input(
                        media=types.Blob(
                            data=chunk,
                            mime_type=f"audio/pcm;rate={PAREC_RATE}"
                        )
                    )
                except Exception as e:
                    err = str(e).lower()
                    if any(k in err for k in (
                        "closed", "websocket", "connection",
                        "reset", "eof", "broken pipe", "stream ended"
                    )):
                        print(f"[send_audio] Connection closed: {e}")
                        return
                    print(f"[send_audio] Skipping chunk: {e}")
                await asyncio.sleep(0.01)

        async def receive_responses():
            audio_buffer = b""
            while not stop_flag.is_set():
                try:
                    async for response in session.receive():
                        try:
                            if (response.server_content and
                                    response.server_content.model_turn):
                                for part in response.server_content.model_turn.parts:
                                    if part.inline_data:
                                        audio_buffer += part.inline_data.data
                                    if hasattr(part, 'text') and part.text:
                                        ts = time.strftime("%H:%M:%S")
                                        print(f"[{ts}] [Chef AI] 🗣️  {part.text}")
                                        with live_lock:
                                            live_state["last_response"] = part.text

                            if (response.server_content and
                                    response.server_content.turn_complete):
                                if audio_buffer:
                                    buf_copy     = audio_buffer
                                    audio_buffer = b""
                                    threading.Thread(
                                        target=player.play,
                                        args=(buf_copy,),
                                        daemon=True
                                    ).start()
                                print("[Live Agent] 🎙️  Ready for next command ...")

                        except Exception as e:
                            print(f"[receive] Inner error (skipping): {e}")
                            continue

                except Exception as e:
                    err = str(e).lower()
                    if any(k in err for k in (
                        "closed", "websocket", "connection", "eof"
                    )):
                        return
                    print(f"[receive_responses] Recoverable: {e}")
                    await asyncio.sleep(0.5)

        async def gas_monitor():
            last_gas = False
            while not stop_flag.is_set():
                with state_lock:
                    gas = kitchen_state.get("gas_detected", False)
                if gas and not last_gas:
                    try:
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part.from_text(
                                    text="ALERT: Gas sensor detected gas!"
                                )]
                            ),
                            turn_complete=True
                        )
                    except Exception:
                        pass
                last_gas = gas
                await asyncio.sleep(2)

        await asyncio.gather(
            send_frames(),
            send_audio(),
            receive_responses(),
            gas_monitor(),
        )

    player.close()
    mic.close()
    with live_lock:
        live_state["connected"] = False


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE AGENT LOOP
# ─────────────────────────────────────────────────────────────────────────────
async def live_agent_loop(picam2: Picamera2):
    client = genai.Client(api_key=GEMINI_API_KEY)
    print(f"[Live Agent] Connecting to {LIVE_MODEL} ...")

    while not stop_flag.is_set():
        try:
            await one_session(picam2, client)
        except Exception as e:
            print(f"[Live Agent] Session ended: {e}")
        if stop_flag.is_set():
            break
        with live_lock:
            live_state["connected"]     = False
            live_state["reconnecting"]  = True
            live_state["last_response"] = f"Reconnecting in {RECONNECT_DELAY}s ..."
        print(f"[Live Agent] Reconnecting in {RECONNECT_DELAY}s ...")
        await asyncio.sleep(RECONNECT_DELAY)


# ─────────────────────────────────────────────────────────────────────────────
#  RUN (called from main.py)
# ─────────────────────────────────────────────────────────────────────────────
def run(picam2: Picamera2):
    print("[Live Agent] ✅ Started — Chef AI initialising ...\n")
    try:
        asyncio.run(live_agent_loop(picam2))
    except Exception as e:
        print(f"[Live Agent] Fatal: {e}")
    print("[Live Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 62)
    print("  🎙️   Chef AI — Gemini Live Agent + Video Stream")
    print("=" * 62 + "\n")

    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY not set!")
        sys.exit(1)

    for tool in ("paplay", "parec"):
        if shutil.which(tool) is None:
            print(f"[ERROR] {tool} not found. Run: sudo apt install pulseaudio-utils")
            sys.exit(1)

    print(f"[Main] API key  : {GEMINI_API_KEY[:8]}...✅")
    print(f"[Main] Model    : {LIVE_MODEL}")
    print(f"[Main] Voice    : {VOICE_NAME}")
    print(f"[Main] Mic src  : {HEADSET_SOURCE}")
    print(f"[Main] Out sink : {HEADSET_SINK}")
    print(f"[Main] Monitor  : {MONITOR_SOURCE}")
    print()
    print("=" * 62)
    print("📹  3-terminal recording setup:")
    print()
    print("  T1 — VIDEO:")
    print("    wf-recorder -f ~/chef_project/demo_video.mkv")
    print()
    print("  T2 — AUDIO (captures AI voice):")
    print(f"    parec -d {MONITOR_SOURCE} \\")
    print("      --format=s16le --rate=48000 --channels=2 \\")
    print("      > ~/chef_project/audio_capture.raw")
    print()
    print("  T3 — AGENT:")
    print("    cd ~/chef_project && python3 agent_live.py")
    print()
    print("  MERGE after Ctrl+C on T1 and T2:")
    print("    ffmpeg -f s16le -ar 48000 -ac 2 \\")
    print("      -i ~/chef_project/audio_capture.raw \\")
    print("      -i ~/chef_project/demo_video.mkv \\")
    print("      -c:v copy -c:a aac ~/chef_project/demo_final.mp4")
    print("=" * 62 + "\n")

    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": DISPLAY_RESOLUTION, "format": "BGR888"},
        controls={"AfMode": 2, "AfRange": 0, "AfSpeed": 1}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    print("[Camera] Ready ✅\n")

    threading.Thread(
        target=vision_scan_loop, args=(picam2,),
        daemon=True, name="VisionScan"
    ).start()
    print("[Main] ✅ Vision scan started")

    threading.Thread(
        target=run, args=(picam2,),
        daemon=True, name="LiveAgent"
    ).start()
    print("[Main] ✅ Live Agent started\n")

    cv2.namedWindow("Chef AI — Live", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Chef AI — Live", *DISPLAY_RESOLUTION)

    try:
        while not stop_flag.is_set():
            frame = picam2.capture_array()
            if ROTATE_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = draw_overlay(frame)
            cv2.imshow("Chef AI — Live", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass

    stop_flag.set()
    cv2.destroyAllWindows()
    time.sleep(1)
    picam2.stop()
    print("[Main] Done. 👋")

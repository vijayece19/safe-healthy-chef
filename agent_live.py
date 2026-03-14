"""
agent_live.py — Gemini Live Agent with Live Video Display
==========================================================
Real-time kitchen assistant using Gemini Live API.
Includes:
  • Continuous multi-turn conversation via VAD + turn_complete
  • Auto-reconnect on dropout
  • Live OpenCV video with full status overlay
  • Built-in vision scan updates pan/gloves/ingredients
  • Gas alert injection
  • DHT11 temperature & humidity display and alert

Hardware:
  Camera Module 3 + USB Microphone + USB Speaker

Dependencies:
  pip install google-genai pyaudio pillow opencv-python

Run standalone:
  python3 agent_live.py
"""

import asyncio
import io
import json
import os
import re
import sys
import time
import threading

import cv2
import numpy as np
import pyaudio
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
LIVE_MODEL           = "gemini-2.5-flash-native-audio-latest"
VOICE_NAME           = "Kore"
FRAME_INTERVAL       = 4.0
SCAN_INTERVAL        = 30.0
AUDIO_SAMPLE_RATE    = 16000
AUDIO_CHUNK          = 2048
RESPONSE_SAMPLE_RATE = 24000
DISPLAY_RESOLUTION   = (1280, 720)
RECONNECT_DELAY      = 5


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
4. ENVIRONMENT: If asked about temperature or humidity, use the sensor data.
   Warn if kitchen temperature exceeds 35 degrees C or humidity exceeds 80%.
5. CONVERSATION: Answer chef questions naturally.

Rules:
- Be concise -- one or two sentences maximum.
- Be proactive -- flag issues immediately when you see them.
- Be friendly -- like a helpful sous-chef.
- If salt looks more than a teaspoon, warn immediately.
- If oil visible, warn this violates the No Oil rule.
- If pan present and no gloves visible on hands, warn immediately.
- After every response, stay ready and listening for the next question.
- NEVER stop listening -- always be ready for the next command.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
#  VISION SCAN PROMPT
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
    """Run Gemini vision scan and update kitchen_state."""
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

        ts = time.strftime("%H:%M:%S")
        result["safety_time"]     = ts
        result["ingredient_time"] = ts

        with state_lock:
            kitchen_state.update(result)

        pan    = result.get("pan_present",    False)
        hands  = result.get("hands_present",  False)
        gloves = result.get("gloves_on",      None)
        status = result.get("overall_status", "?")
        print(
            f"[{ts}] [Vision] "
            f"Pan:{pan} Hands:{hands} "
            f"Gloves:{gloves} Status:{status}"
        )
    except Exception as e:
        print(f"[Vision Scan] Error: {e}")


def vision_scan_loop(picam2: Picamera2):
    """FIX 1 -- prints temperature & humidity alongside every vision scan."""
    time.sleep(5)
    while not stop_flag.is_set():
        run_vision_scan(picam2)

        with state_lock:
            temp = kitchen_state.get("temperature", None)
            hum  = kitchen_state.get("humidity",    None)
        temp_str = f"{temp} degrees C" if temp is not None else "no reading yet"
        hum_str  = f"{hum}%"           if hum  is not None else "no reading yet"
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [Live Agent] Temp: {temp_str}   Humidity: {hum_str}")

        time.sleep(SCAN_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIO PLAYER
# ─────────────────────────────────────────────────────────────────────────────
class AudioPlayer:
    def __init__(self):
        self.pa     = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format            = pyaudio.paInt16,
            channels          = 1,
            rate              = RESPONSE_SAMPLE_RATE,
            output            = True,
            frames_per_buffer = AUDIO_CHUNK,
        )
        print("[Audio] Speaker ready")

    def play(self, data: bytes):
        try:
            with live_lock:
                live_state["ai_speaking"] = True
            self.stream.write(data)
        except Exception as e:
            print(f"[Audio] Error: {e}")
        finally:
            with live_lock:
                live_state["ai_speaking"] = False

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
            self.pa.terminate()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  MICROPHONE READER
# ─────────────────────────────────────────────────────────────────────────────
class MicrophoneReader:
    def __init__(self):
        self.pa     = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format            = pyaudio.paInt16,
            channels          = 1,
            rate              = AUDIO_SAMPLE_RATE,
            input             = True,
            frames_per_buffer = AUDIO_CHUNK,
        )
        print("[Mic] Microphone ready")

    def read_chunk(self) -> bytes:
        return self.stream.read(AUDIO_CHUNK, exception_on_overflow=False)

    def close(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
            self.pa.terminate()
        except Exception:
            pass


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

    # Top panel expanded to fit DHT11 row
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 195), BLACK, -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    # LIVE dot
    cv2.circle(frame, (w - 28, 22), 9, RED, -1)
    cv2.putText(frame, "LIVE",
                (w - 70, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, RED, 2)

    # Chef AI status indicator
    if ls["reconnecting"]:
        ai_color, ai_label = ORANGE, "RECONNECTING"
    elif ls["ai_speaking"]:
        ai_color, ai_label = PURPLE, "SPEAKING"
    elif ls["connected"]:
        ai_color, ai_label = CYAN, "LISTENING"
    else:
        ai_color, ai_label = GREY, "OFFLINE"

    cv2.circle(frame, (w - 28, 55), 7, ai_color, -1)
    cv2.putText(frame, f"AI:{ai_label}",
                (w - 165, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.45, ai_color, 1)

    # Row 1 -- Safety
    cv2.putText(frame, "SAFETY:", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    pan_val = s.get("pan_present", False)
    cv2.putText(frame, f"PAN: {'YES' if pan_val else 'NO'}", (110, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, GREEN if pan_val else GREY, 2)
    hands_val = s.get("hands_present", False)
    cv2.putText(frame, f"HANDS: {'YES' if hands_val else 'NO'}", (270, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, GREEN if hands_val else GREY, 2)
    gloves_val = s.get("gloves_on", None)
    glove_col  = GREEN if gloves_val else RED if gloves_val is False else GREY
    cv2.putText(frame, f"GLOVES: {str(gloves_val).upper()}", (480, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, glove_col, 2)
    cv2.putText(frame, f"[{s.get('safety_time', '--:--:--')}]",
                (w - 160, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # Row 2 -- Ingredients
    cv2.putText(frame, "INGREDIENTS:", (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    salt_val   = s.get("salt_level",   "unknown")
    chilli_val = s.get("chilli_level", "unknown")
    oil_val    = s.get("oil_visible",  False)
    salt_col   = RED if salt_val   == "too_much" else GREEN if salt_val   != "unknown" else GREY
    chilli_col = RED if chilli_val == "too_much" else GREEN if chilli_val != "unknown" else GREY
    cv2.putText(frame, f"SALT: {salt_val.upper()}", (200, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, salt_col, 2)
    cv2.putText(frame, f"CHILLI: {chilli_val.upper()}", (480, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, chilli_col, 2)
    cv2.putText(frame, f"OIL: {'YES' if oil_val else 'NO'}", (760, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, RED if oil_val else GREEN, 2)
    cv2.putText(frame, f"[{s.get('ingredient_time', '--:--:--')}]",
                (w - 160, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # Row 3 -- Gas + Kitchen status
    gas_val     = s.get("gas_detected",   False)
    kitchen_val = s.get("overall_status", "unknown")
    cv2.putText(frame, "GAS:", (10, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, "DETECTED!" if gas_val else "CLEAR", (75, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, RED if gas_val else GREEN, 2)
    kitchen_col = RED if kitchen_val == "danger" else ORANGE if kitchen_val == "warning" else GREEN
    cv2.putText(frame, f"STATUS: {kitchen_val.upper()}", (280, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, kitchen_col, 2)
    vegs    = s.get("vegetables", [])
    veg_str = ", ".join(vegs[:3]) if vegs else "none"
    cv2.putText(frame, f"VEG: {veg_str}", (600, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, WHITE, 1)

    # Row 4 -- DHT11 Temperature & Humidity
    temp = s.get("temperature", None)
    hum  = s.get("humidity",    None)
    temp_str = f"{temp}C" if temp is not None else "---"
    hum_str  = f"{hum}%"  if hum  is not None else "---"
    temp_col = RED if (temp is not None and temp > 35) else GREEN if temp is not None else GREY
    hum_col  = RED if (hum  is not None and hum  > 80) else GREEN if hum  is not None else GREY
    cv2.putText(frame, "TEMP:", (10, 133),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, temp_str, (90, 133),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, temp_col, 2)
    cv2.putText(frame, "HUMIDITY:", (220, 133),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, hum_str, (390, 133),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, hum_col, 2)
    cv2.putText(frame, f"[{s.get('dht_time', '--:--:--')}]",
                (w - 160, 133), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # Row 5 -- Chef AI last response
    cv2.putText(frame, f"Chef AI: {ls['last_response'][:72]}", (10, 163),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, CYAN, 1)

    # Row 6 -- Mic hint
    cv2.putText(frame,
                "Speak: 'What do you see?' | 'Is it safe?' | 'Check the salt'",
                (10, 188), cv2.FONT_HERSHEY_SIMPLEX, 0.44, GREY, 1)

    # Alert banners (bottom)
    banner_y = h

    if gas_val:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 0, 180), -1)
        cv2.putText(frame, "  GAS DETECTED -- CHECK KITCHEN IMMEDIATELY!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, WHITE, 2)

    if s.get("safety_alert", False):
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 60, 180), -1)
        cv2.putText(frame, "  SAFETY: Pan detected -- Please wear your gloves!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if oil_val:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 100, 200), -1)
        cv2.putText(frame, "  OIL DETECTED -- This is a No Oil recipe!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if salt_val == "too_much":
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (30, 100, 180), -1)
        cv2.putText(frame, "  TOO MUCH SALT -- Reduce for a healthier salad!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.78, WHITE, 2)

    if chilli_val == "too_much":
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 80, 200), -1)
        cv2.putText(frame, "  TOO MUCH CHILLI -- This may be too spicy!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if temp is not None and temp > 35:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 60, 200), -1)
        cv2.putText(frame, f"  HIGH TEMP: {temp}C -- Kitchen is very hot!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

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
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=200,
                silence_duration_ms=800,
            )
        ),
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
            live_state["last_response"] = "Chef AI is listening -- speak freely!"
        print("[Live Agent] Connected -- speak freely!\n")

        async def send_frames():
            last_time = 0
            while not stop_flag.is_set():
                now = time.time()
                if now - last_time >= FRAME_INTERVAL:
                    try:
                        jpeg = capture_jpeg(picam2)
                        await session.send_realtime_input(
                            media=types.Blob(
                                data=jpeg,
                                mime_type="image/jpeg"
                            )
                        )
                        last_time = now
                    except Exception:
                        return
                await asyncio.sleep(0.2)

        async def send_audio():
            loop = asyncio.get_event_loop()
            while not stop_flag.is_set():
                try:
                    chunk = await loop.run_in_executor(None, mic.read_chunk)
                    await session.send_realtime_input(
                        media=types.Blob(
                            data=chunk,
                            mime_type="audio/pcm"
                        )
                    )
                except Exception:
                    return
                await asyncio.sleep(0.05)

        async def receive_responses():
            async for response in session.receive():
                try:
                    if (response.server_content and
                            response.server_content.model_turn):
                        for part in response.server_content.model_turn.parts:
                            if part.inline_data:
                                player.play(part.inline_data.data)
                            if hasattr(part, "text") and part.text:
                                ts = time.strftime("%H:%M:%S")
                                print(f"[{ts}] [Chef AI] {part.text}")
                                with live_lock:
                                    live_state["last_response"] = part.text

                    if (response.server_content and
                            response.server_content.turn_complete):
                        print("[Live Agent] Ready for next command ...")
                        with live_lock:
                            live_state["ai_speaking"] = False
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=" ")]
                            ),
                            turn_complete=False
                        )

                except Exception as e:
                    print(f"[Live Agent] Receive error: {e}")
                    return

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
                                    text="SYSTEM ALERT: MQ2 gas sensor detected gas!"
                                )]
                            ),
                            turn_complete=True
                        )
                    except Exception:
                        pass
                last_gas = gas
                await asyncio.sleep(2)

        async def temp_monitor():
            last_alert = False
            while not stop_flag.is_set():
                with state_lock:
                    temp = kitchen_state.get("temperature", None)
                high = temp is not None and temp > 35
                if high and not last_alert:
                    try:
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part.from_text(
                                    text=f"SYSTEM ALERT: Kitchen temperature is {temp} degrees C -- very hot!"
                                )]
                            ),
                            turn_complete=True
                        )
                    except Exception:
                        pass
                last_alert = high
                await asyncio.sleep(10)

        await asyncio.gather(
            send_frames(),
            send_audio(),
            receive_responses(),
            gas_monitor(),
            temp_monitor(),
        )

    player.close()
    mic.close()
    with live_lock:
        live_state["connected"] = False


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE AGENT LOOP -- auto reconnects
# ─────────────────────────────────────────────────────────────────────────────
async def live_agent_loop(picam2: Picamera2):
    client = genai.Client(api_key=GEMINI_API_KEY)
    print(f"[Live Agent] Connecting to {LIVE_MODEL} ...")

    while not stop_flag.is_set():
        try:
            await one_session(picam2, client)
        except Exception as e:
            print(f"[Live Agent] Disconnected: {e}")

        if stop_flag.is_set():
            break

        with live_lock:
            live_state["connected"]    = False
            live_state["reconnecting"] = True
            live_state["last_response"]= f"Reconnecting in {RECONNECT_DELAY}s ..."

        print(f"[Live Agent] Reconnecting in {RECONNECT_DELAY}s ...")
        await asyncio.sleep(RECONNECT_DELAY)


# ─────────────────────────────────────────────────────────────────────────────
#  RUN (called from main.py)
# ─────────────────────────────────────────────────────────────────────────────
def run(picam2: Picamera2):
    print("[Live Agent] Started -- Chef AI initialising ...\n")
    try:
        asyncio.run(live_agent_loop(picam2))
    except Exception as e:
        print(f"[Live Agent] Fatal: {e}")
    print("[Live Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE -- with live OpenCV window
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 58)
    print("  Chef AI -- Gemini Live Agent + Video Stream")
    print("=" * 58 + "\n")

    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY not set!")
        sys.exit(1)

    print(f"[Main]  API key : {GEMINI_API_KEY[:8]}...OK")
    print(f"[Main]  Model  : {LIVE_MODEL}")
    print(f"[Main]  Voice  : {VOICE_NAME}\n")

    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": DISPLAY_RESOLUTION, "format": "BGR888"},
        controls={"AfMode": 2, "AfRange": 0, "AfSpeed": 1}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    print("[Camera] Ready\n")

    # Vision scan background thread
    threading.Thread(
        target=vision_scan_loop, args=(picam2,),
        daemon=True, name="VisionScan"
    ).start()
    print("[Main]  Vision scan started (every 30s)")

    # FIX 2 -- DHT11 agent started so temp/humidity flows into kitchen_state
    import agent_dht11
    threading.Thread(
        target=agent_dht11.run,
        daemon=True,
        name="DHT11Agent"
    ).start()
    print("[Main]  DHT11 Agent started (GPIO 17)")

    # Live Agent background thread
    threading.Thread(
        target=run, args=(picam2,),
        daemon=True, name="LiveAgent"
    ).start()
    print("[Main]  Live Agent started")

    print("\n[Display] Live window -- speak freely -- press q to quit\n")
    cv2.namedWindow("Chef AI -- Live", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Chef AI -- Live", *DISPLAY_RESOLUTION)

    try:
        while not stop_flag.is_set():
            frame = picam2.capture_array()
            if ROTATE_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = draw_overlay(frame)
            cv2.imshow("Chef AI -- Live", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        pass

    stop_flag.set()
    cv2.destroyAllWindows()
    time.sleep(1)
    picam2.stop()
    print("[Main]  Done.")

"""
agent_safety.py — Safety Agent
================================
Monitors cooking pan presence and glove usage via Gemini Vision.
Scans every 60 seconds using Camera Module 3.
Triggers voice alert if pan present but no gloves worn.

FIX: Replaced pyttsx3 with paplay subprocess.
     pyttsx3 uses ALSA directly — conflicts with PipeWire.
     paplay routes through PipeWire → captured by wf-recorder monitor.

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import io
import json
import re
import subprocess
import threading
import time
from datetime import datetime

from google import genai
from google.genai import types
from PIL import Image

from config import (
    kitchen_state, state_lock, stop_flag,
    GEMINI_API_KEY, GEMINI_MODEL,
    SAFETY_INTERVAL, ALERT_COOLDOWN, ROTATE_180
)

# ─────────────────────────────────────────────────────────────────────────────
#  TTS HELPER — paplay via PipeWire
# ─────────────────────────────────────────────────────────────────────────────
_tts_lock        = threading.Lock()
_last_alert_time = 0

HEADSET_SINK = (
    "alsa_output.usb-USB_PnP_Sound_Device_USB_PnP_Sound_Device"
    "-00.analog-stereo"
)


def speak(message: str):
    global _last_alert_time
    now = time.time()
    if now - _last_alert_time < ALERT_COOLDOWN:
        return
    with _tts_lock:
        _last_alert_time = now
        print(f"[Safety Agent TTS] 🔊 '{message}'")
        try:
            espeak = subprocess.Popen(
                ["espeak", "-s", "130", "--stdout", message],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            paplay = subprocess.Popen(
                ["paplay", "-d", HEADSET_SINK],
                stdin=espeak.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            espeak.stdout.close()
            paplay.wait()
        except Exception as e:
            print(f"[Safety Agent TTS] Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI SETUP
# ─────────────────────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

SAFETY_PROMPT = """
You are a kitchen safety vision system.
Examine the image:
1. Is there a cooking pan (frying pan, saucepan, wok, skillet) visible?
2. Are human hands visible? Are ALL hands wearing safety/oven gloves?

Rules:
- gloves_on=true ONLY if every visible hand has a glove
- No hands visible: hands_present=false, gloves_on=null
- If unsure about glove, treat as absent

Respond ONLY with valid JSON, no markdown fences:
{
  "pan_present":   <true|false>,
  "hands_present": <true|false>,
  "gloves_on":     <true|false|null>,
  "confidence":    <"high"|"medium"|"low">,
  "notes":         "<one sentence describing what you see>"
}
""".strip()


def analyze(pil_image: Image.Image) -> dict | None:
    """Send image to Gemini and return parsed safety JSON."""
    raw = ""
    try:
        buf = io.BytesIO()
        pil_image.convert("RGB").save(buf, format="JPEG", quality=85)
        image_part = types.Part.from_bytes(
            data=buf.getvalue(), mime_type="image/jpeg"
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=[
                types.Part.from_text(text=SAFETY_PROMPT),
                image_part
            ])]
        )
        raw = response.text.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        return json.loads(raw)

    except json.JSONDecodeError as e:
        print(f"[Safety Agent] ❌ JSON error: {e} | Raw: {raw[:150]}")
        return None
    except Exception as e:
        error_str = str(e)
        if "429" in error_str:
            print("[Safety Agent] ⏳ Quota hit — waiting 60s ...")
            time.sleep(60)
        else:
            print(f"[Safety Agent] ❌ {error_str[:150]}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  SAFETY AGENT
# ─────────────────────────────────────────────────────────────────────────────
def run(picam2):
    """
    Safety Agent main loop.
    Captures frame every SAFETY_INTERVAL seconds.
    Sends to Gemini for pan + gloves analysis.
    """
    print("[Safety Agent] ✅ Started — scanning every "
          f"{SAFETY_INTERVAL}s\n")

    while not stop_flag.is_set():
        try:
            frame   = picam2.capture_array()
            pil_img = Image.fromarray(frame)
            if ROTATE_180:
                pil_img = pil_img.rotate(180)

            result = analyze(pil_img)

            if result:
                pan    = result.get("pan_present",   False)
                hands  = result.get("hands_present", False)
                gloves = result.get("gloves_on",     None)
                alert  = pan and hands and (gloves is False)
                timestamp = datetime.now().strftime("%H:%M:%S")

                with state_lock:
                    kitchen_state.update({
                        "pan_present":   pan,
                        "hands_present": hands,
                        "gloves_on":     gloves,
                        "safety_alert":  alert,
                        "safety_notes":  result.get("notes", ""),
                        "safety_time":   timestamp,
                        "safety_conf":   result.get("confidence", "low"),
                    })

                icon = "🚨" if alert else "✅"
                print(
                    f"[{timestamp}] [Safety Agent] {icon}"
                    f"\n  Pan: {'YES' if pan else 'NO'} | "
                    f"Hands: {'YES' if hands else 'NO'} | "
                    f"Gloves: {str(gloves)} | "
                    f"Conf: {result.get('confidence','?')}"
                    f"\n  👁  {result.get('notes','')}\n"
                )

                if alert:
                    threading.Thread(
                        target=speak,
                        args=("Safety first! Pan detected but no gloves. "
                              "Please wear your gloves!",),
                        daemon=True
                    ).start()

        except Exception as e:
            print(f"[Safety Agent] ❌ Error: {e}")

        time.sleep(SAFETY_INTERVAL)

    print("[Safety Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN STANDALONE (for testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from picamera2 import Picamera2

    print("=" * 45)
    print("  🧤  Safety Agent — Standalone Test")
    print("=" * 45 + "\n")

    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": (1920, 1080)},
        controls={"AfMode": 2, "AfRange": 0, "AfSpeed": 1}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)

    print("Press Ctrl+C to stop.\n")
    try:
        run(picam2)
    except KeyboardInterrupt:
        stop_flag.set()
        picam2.stop()
        print("\n[Safety Agent] Stopped by user.")

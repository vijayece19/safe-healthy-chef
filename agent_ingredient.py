"""
agent_ingredient.py — Ingredient Agent
========================================
Analyses tomato & carrot salad ingredients via Gemini Vision.
Detects salt level, chilli powder, oil presence, vegetables.
Scans every 30 seconds using Camera Module 3.
Triggers voice alert for unhealthy ingredient levels.

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
    INGREDIENT_INTERVAL, ALERT_COOLDOWN, ROTATE_180
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
        print(f"[Ingredient Agent TTS] 🔊 '{message}'")
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
            print(f"[Ingredient Agent TTS] Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI SETUP
# ─────────────────────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

INGREDIENT_PROMPT = """
You are a healthy cooking assistant.
The chef is making a No Oil No Boil Tomato and Carrot Salad.

Carefully analyse the bowl or plate in the image:
1. Salt: how much white granular salt is visible?
2. Red chilli powder: how much red powder is visible?
3. Oil: is there any shiny oily surface visible?
4. Vegetables: list all vegetables you can identify

Warnings to flag:
- too_much salt is unhealthy
- too_much chilli is too spicy
- oil_visible violates the No Oil rule

Respond ONLY with valid JSON, no markdown fences:
{
  "salt_level":     <"none"|"pinch"|"teaspoon"|"too_much">,
  "chilli_level":   <"none"|"light"|"moderate"|"too_much">,
  "oil_visible":    <true|false>,
  "vegetables":     ["list", "of", "vegetables"],
  "warnings":       ["list of warnings if any"],
  "overall_status": <"healthy"|"warning"|"danger">,
  "confidence":     <"high"|"medium"|"low">,
  "notes":          "<one sentence summary>"
}
""".strip()


def analyze(pil_image: Image.Image) -> dict | None:
    """Send image to Gemini and return parsed ingredient JSON."""
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
                types.Part.from_text(text=INGREDIENT_PROMPT),
                image_part
            ])]
        )
        raw = response.text.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        return json.loads(raw)

    except json.JSONDecodeError as e:
        print(f"[Ingredient Agent] ❌ JSON error: {e} | Raw: {raw[:150]}")
        return None
    except Exception as e:
        error_str = str(e)
        if "429" in error_str:
            print("[Ingredient Agent] ⏳ Quota hit — waiting 60s ...")
            time.sleep(60)
        else:
            print(f"[Ingredient Agent] ❌ {error_str[:150]}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  INGREDIENT AGENT
# ─────────────────────────────────────────────────────────────────────────────
def run(picam2):
    """
    Ingredient Agent main loop.
    Captures frame every INGREDIENT_INTERVAL seconds.
    Sends to Gemini for salt/chilli/oil/vegetable analysis.
    """
    print("[Ingredient Agent] ✅ Started — scanning every "
          f"{INGREDIENT_INTERVAL}s\n")

    while not stop_flag.is_set():
        try:
            frame   = picam2.capture_array()
            pil_img = Image.fromarray(frame)
            if ROTATE_180:
                pil_img = pil_img.rotate(180)

            result = analyze(pil_img)

            if result:
                salt   = result.get("salt_level",    "unknown")
                chilli = result.get("chilli_level",  "unknown")
                oil    = result.get("oil_visible",   False)
                vegs   = result.get("vegetables",    [])
                warns  = result.get("warnings",      [])
                status = result.get("overall_status","unknown")
                timestamp = datetime.now().strftime("%H:%M:%S")

                with state_lock:
                    kitchen_state.update({
                        "salt_level":       salt,
                        "chilli_level":     chilli,
                        "oil_visible":      oil,
                        "vegetables":       vegs,
                        "warnings":         warns,
                        "overall_status":   status,
                        "ingredient_notes": result.get("notes", ""),
                        "ingredient_time":  timestamp,
                    })

                icon = "✅" if status == "healthy" else "⚠️" if status == "warning" else "🚨"
                print(
                    f"[{timestamp}] [Ingredient Agent] {icon}"
                    f"\n  Salt: {salt:12} | Chilli: {chilli:12} | "
                    f"Oil: {'YES ⚠️' if oil else 'NO ✅'}"
                    f"\n  Vegetables: {', '.join(vegs) if vegs else 'none'}"
                    f"\n  Warnings:   {', '.join(warns) if warns else 'none'}"
                    f"\n  👁  {result.get('notes','')}\n"
                )

                # ── Voice alerts ──────────────────────────────────────────────
                if oil:
                    threading.Thread(
                        target=speak,
                        args=("Warning! Oil detected. This is a No Oil recipe!",),
                        daemon=True
                    ).start()
                elif salt == "too_much":
                    threading.Thread(
                        target=speak,
                        args=("Too much salt! Please reduce for a healthier salad.",),
                        daemon=True
                    ).start()
                elif chilli == "too_much":
                    threading.Thread(
                        target=speak,
                        args=("Too much chilli! This may be too spicy.",),
                        daemon=True
                    ).start()

        except Exception as e:
            print(f"[Ingredient Agent] ❌ Error: {e}")

        time.sleep(INGREDIENT_INTERVAL)

    print("[Ingredient Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN STANDALONE (for testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from picamera2 import Picamera2

    print("=" * 45)
    print("  🧂  Ingredient Agent — Standalone Test")
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
        print("\n[Ingredient Agent] Stopped by user.")

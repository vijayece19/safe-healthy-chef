"""
agent_gas.py — Gas Agent
=========================
Monitors MQ2 gas sensor on GPIO 4 every 2 seconds.
Updates shared kitchen_state with gas detection status.
Triggers voice alert if gas is detected.

FIX: Replaced pyttsx3 with paplay subprocess.
     pyttsx3 uses ALSA directly — conflicts with PipeWire.
     paplay routes through PipeWire → captured by wf-recorder monitor.

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import lgpio
import subprocess
import threading
import time
from datetime import datetime

from config import (
    kitchen_state, state_lock, stop_flag,
    GAS_PIN, GAS_INTERVAL, ALERT_COOLDOWN
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
        print(f"[Gas Agent TTS] 🔊 '{message}'")
        try:
            # Convert text to speech WAV via espeak, pipe to paplay
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
            print(f"[Gas Agent TTS] Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  GAS AGENT
# ─────────────────────────────────────────────────────────────────────────────
def run():
    """
    Gas Agent main loop.
    Reads MQ2 DO pin every GAS_INTERVAL seconds.
    DO = LOW (0) means gas detected.
    DO = HIGH (1) means air is clear.
    """
    handle = lgpio.gpiochip_open(0)
    lgpio.gpio_claim_input(handle, GAS_PIN)
    print("[Gas Agent] ✅ Started — monitoring MQ2 on GPIO 4\n")

    while not stop_flag.is_set():
        try:
            value = lgpio.gpio_read(handle, GAS_PIN)
            gas   = (value == 0)
            timestamp = datetime.now().strftime("%H:%M:%S")

            with state_lock:
                kitchen_state["gas_detected"] = gas

            if gas:
                print(f"[{timestamp}] [Gas Agent] 🚨 GAS DETECTED!")
                threading.Thread(
                    target=speak,
                    args=("Warning! Gas detected in the kitchen! Please check immediately.",),
                    daemon=True
                ).start()
            else:
                print(f"[{timestamp}] [Gas Agent] ✅ Air Clear")

        except Exception as e:
            print(f"[Gas Agent] ❌ Error: {e}")

        time.sleep(GAS_INTERVAL)

    lgpio.gpiochip_close(handle)
    print("[Gas Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN STANDALONE (for testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 45)
    print("  🔥  Gas Agent — Standalone Test")
    print("=" * 45 + "\n")
    print("Press Ctrl+C to stop.\n")
    try:
        run()
    except KeyboardInterrupt:
        stop_flag.set()
        print("\n[Gas Agent] Stopped by user.")

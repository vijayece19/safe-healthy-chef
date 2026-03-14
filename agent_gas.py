"""
agent_gas.py — Gas Agent
=========================
Monitors MQ2 gas sensor on GPIO 4 every 2 seconds.
Updates shared kitchen_state with gas detection status.
Triggers voice alert if gas is detected.

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import lgpio
import threading
import time
from datetime import datetime

import pyttsx3

from config import (
    kitchen_state, state_lock, stop_flag,
    GAS_PIN, GAS_INTERVAL, ALERT_COOLDOWN
)

# ─────────────────────────────────────────────────────────────────────────────
#  TTS HELPER
# ─────────────────────────────────────────────────────────────────────────────
_tts_lock        = threading.Lock()
_last_alert_time = 0


def speak(message: str):
    global _last_alert_time
    now = time.time()
    if now - _last_alert_time < ALERT_COOLDOWN:
        return
    with _tts_lock:
        _last_alert_time = now
        print(f"[Gas Agent TTS] 🔊 '{message}'")
        engine = pyttsx3.init()
        engine.setProperty("rate", 155)
        engine.setProperty("volume", 1.0)
        engine.say(message)
        engine.runAndWait()
        engine.stop()


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

"""
main.py — Safe & Healthy Chef: Master Controller
==================================================
Starts all 7 agents and runs the live OpenCV display.

Architecture:
  config.py             ← shared state & settings
  agent_gas.py          ← Gas Agent (MQ2 sensor)
  agent_safety.py       ← Safety Agent (pan + gloves)
  agent_ingredient.py   ← Ingredient Agent (salt/chilli/oil)
  agent_orchestrator.py ← Orchestrator Agent (coordinates all)
  agent_live.py         ← Gemini Live Agent (real-time voice)
  agent_dht11.py        ← DHT11 Temperature & Humidity Agent
  agent_storage.py      ← Google Cloud Storage Agent
  main.py               ← YOU ARE HERE (runs everything)

Hardware:
  Camera Module 3 + MQ2 + DHT11 + USB Mic + USB Speaker

Dependencies:
  pip install google-genai google-cloud-storage picamera2
              pyttsx3 lgpio Pillow opencv-python pyaudio

Setup:
  export GEMINI_API_KEY="your_key_here"
  Copy gcs-key.json to ~/chef_project/

Run:
  python3 main.py
"""

import threading
import time
import cv2
import numpy as np
import pyttsx3
from picamera2 import Picamera2

# ── Import all agents ─────────────────────────────────────────────────────────
import agent_gas
import agent_safety
import agent_ingredient
import agent_orchestrator
import agent_live
import agent_dht11
import agent_storage

# ── Import shared config ──────────────────────────────────────────────────────
from config import (
    kitchen_state, state_lock, stop_flag,
    GEMINI_API_KEY, DISPLAY_RESOLUTION,
    ROTATE_180, ALERT_COOLDOWN
)

# ── Import live state from agent_live ─────────────────────────────────────────
from agent_live import live_state, live_lock


# ─────────────────────────────────────────────────────────────────────────────
#  TTS HELPER (startup message only)
# ─────────────────────────────────────────────────────────────────────────────
def speak(message: str):
    engine = pyttsx3.init()
    engine.setProperty("rate", 155)
    engine.setProperty("volume", 1.0)
    engine.say(message)
    engine.runAndWait()
    engine.stop()


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE OVERLAY DRAWING
# ─────────────────────────────────────────────────────────────────────────────
def draw_overlay(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]

    with state_lock:
        s = kitchen_state.copy()
    with live_lock:
        ls = live_state.copy()

    GREEN  = (0, 200, 0)
    RED    = (0, 0, 220)
    ORANGE = (0, 140, 255)
    GREY   = (120, 120, 120)
    WHITE  = (255, 255, 255)
    BLACK  = (0, 0, 0)
    YELLOW = (0, 220, 220)
    CYAN   = (255, 220, 0)
    PURPLE = (220, 100, 220)

    # ── Top panel ─────────────────────────────────────────────────────────────
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 190), BLACK, -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    # ── LIVE dot ──────────────────────────────────────────────────────────────
    cv2.circle(frame, (w - 28, 22), 9, RED, -1)
    cv2.putText(frame, "LIVE",
                (w - 70, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, RED, 2)

    # ── Chef AI status ────────────────────────────────────────────────────────
    if ls["reconnecting"]:
        ai_color, ai_label = ORANGE, "RECONNECTING"
    elif ls["ai_speaking"]:
        ai_color, ai_label = PURPLE, "SPEAKING"
    elif ls["connected"]:
        ai_color, ai_label = CYAN, "LISTENING"
    else:
        ai_color, ai_label = GREY, "OFFLINE"

    cv2.circle(frame, (w - 28, 52), 7, ai_color, -1)
    cv2.putText(frame, f"AI:{ai_label}",
                (w - 165, 57), cv2.FONT_HERSHEY_SIMPLEX, 0.45, ai_color, 1)

    # ── GCS indicator ─────────────────────────────────────────────────────────
    cv2.putText(frame, "☁️ GCS",
                (w - 165, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.42, GREEN, 1)

    # ── Row 1 — Safety ────────────────────────────────────────────────────────
    cv2.putText(frame, "SAFETY:",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, f"PAN: {'YES' if s['pan_present'] else 'NO'}",
                (110, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                GREEN if s['pan_present'] else GREY, 2)
    cv2.putText(frame, f"HANDS: {'YES' if s['hands_present'] else 'NO'}",
                (270, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                GREEN if s['hands_present'] else GREY, 2)
    glove_col = GREEN if s['gloves_on'] else RED if s['gloves_on'] is False else GREY
    cv2.putText(frame, f"GLOVES: {str(s['gloves_on']).upper()}",
                (480, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, glove_col, 2)
    cv2.putText(frame, f"[{s['safety_time']}]",
                (w - 160, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # ── Row 2 — Ingredients ───────────────────────────────────────────────────
    cv2.putText(frame, "INGREDIENTS:",
                (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    salt_col   = RED if s['salt_level'] == "too_much" else GREEN if s['salt_level'] != "unknown" else GREY
    chilli_col = RED if s['chilli_level'] == "too_much" else GREEN if s['chilli_level'] != "unknown" else GREY
    cv2.putText(frame, f"SALT: {s['salt_level'].upper()}",
                (200, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, salt_col, 2)
    cv2.putText(frame, f"CHILLI: {s['chilli_level'].upper()}",
                (480, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, chilli_col, 2)
    cv2.putText(frame, f"OIL: {'YES' if s['oil_visible'] else 'NO'}",
                (760, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                RED if s['oil_visible'] else GREEN, 2)
    cv2.putText(frame, f"[{s['ingredient_time']}]",
                (w - 160, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # ── Row 3 — Gas + Kitchen ─────────────────────────────────────────────────
    cv2.putText(frame, "GAS:",
                (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame,
                "DETECTED!" if s['gas_detected'] else "CLEAR",
                (75, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                RED if s['gas_detected'] else GREEN, 2)
    kitchen_col = RED if s['kitchen_status'] == "danger" else ORANGE if s['kitchen_status'] == "warning" else GREEN
    cv2.putText(frame, f"KITCHEN: {s['kitchen_status'].upper()}",
                (280, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.65, kitchen_col, 2)
    vegs = ', '.join(s['vegetables'][:3]) if s['vegetables'] else "none"
    cv2.putText(frame, f"VEG: {vegs}",
                (600, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.52, WHITE, 1)

    # ── Row 4 — Temperature + Humidity (DHT11) ────────────────────────────────
    temp = s.get("temperature")
    hum  = s.get("humidity")
    dht  = s.get("dht_time", "--:--:--")
    temp_str = f"{temp}°C" if temp is not None else "—"
    hum_str  = f"{hum}%"   if hum  is not None else "—"
    temp_col = RED if temp is not None and temp > 35 else GREEN if temp is not None else GREY
    hum_col  = RED if hum  is not None and hum  > 80 else GREEN if hum  is not None else GREY
    cv2.putText(frame, "ENV:",
                (10, 133), cv2.FONT_HERSHEY_SIMPLEX, 0.65, YELLOW, 2)
    cv2.putText(frame, f"TEMP: {temp_str}",
                (80, 133), cv2.FONT_HERSHEY_SIMPLEX, 0.65, temp_col, 2)
    cv2.putText(frame, f"HUMIDITY: {hum_str}",
                (310, 133), cv2.FONT_HERSHEY_SIMPLEX, 0.65, hum_col, 2)
    cv2.putText(frame, f"[{dht}]",
                (w - 160, 133), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREY, 1)

    # ── Row 5 — Chef AI last response ─────────────────────────────────────────
    cv2.putText(frame, f"Chef AI: {ls['last_response'][:70]}",
                (10, 162), cv2.FONT_HERSHEY_SIMPLEX, 0.48, CYAN, 1)

    # ── Row 6 — Status ────────────────────────────────────────────────────────
    cv2.putText(frame, f"🎙️  {ai_label} — speak your question",
                (10, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.44, ai_color, 1)

    # ── Alert banners ─────────────────────────────────────────────────────────
    banner_y = h

    if s['gas_detected']:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 0, 180), -1)
        cv2.putText(frame, "  GAS DETECTED — CHECK KITCHEN IMMEDIATELY!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, WHITE, 2)

    if s['safety_alert']:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 60, 180), -1)
        cv2.putText(frame, "  SAFETY: Pan detected — Please wear your gloves!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if s['oil_visible']:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 100, 200), -1)
        cv2.putText(frame, "  OIL DETECTED — This is a No Oil recipe!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if s['salt_level'] == "too_much":
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (30, 100, 180), -1)
        cv2.putText(frame, "  TOO MUCH SALT — Please reduce for a healthier salad!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.78, WHITE, 2)

    if s['chilli_level'] == "too_much":
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 80, 200), -1)
        cv2.putText(frame, "  TOO MUCH CHILLI — This may be too spicy!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if temp is not None and temp > 35:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (0, 60, 140), -1)
        cv2.putText(frame, f"  HIGH TEMPERATURE: {temp}°C — Kitchen is very hot!",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2)

    if ls["ai_speaking"]:
        banner_y -= 55
        cv2.rectangle(frame, (0, banner_y), (w, banner_y + 55), (80, 20, 80), -1)
        cv2.putText(frame, "  Chef AI is speaking ...",
                    (10, banner_y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.85, WHITE, 2)

    return frame


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  🍳  Safe & Healthy Chef — Multi-Agent AI System")
    print("=" * 60)
    print()
    print("  Agents:")
    print("  ├── 🔥 Gas Agent          (agent_gas.py)")
    print("  ├── 🧤 Safety Agent       (agent_safety.py)")
    print("  ├── 🧂 Ingredient Agent   (agent_ingredient.py)")
    print("  ├── 🎯 Orchestrator       (agent_orchestrator.py)")
    print("  ├── 🎙️  Chef AI Live       (agent_live.py)")
    print("  ├── 🌡️  DHT11 Agent        (agent_dht11.py)")
    print("  └── ☁️  Cloud Storage      (agent_storage.py)")
    print()

    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY not set!")
        print("  Run: export GEMINI_API_KEY='your_key_here'\n")
        return

    print(f"[Main]  API key  : {GEMINI_API_KEY[:8]}...✅")
    print(f"[Main]  Display  : {DISPLAY_RESOLUTION[0]}x{DISPLAY_RESOLUTION[1]}")
    print(f"[Main]  Rotation : {'180°' if ROTATE_180 else 'None'}\n")

    # ── Camera ────────────────────────────────────────────────────────────────
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": DISPLAY_RESOLUTION, "format": "BGR888"},
        controls={"AfMode": 2, "AfRange": 0, "AfSpeed": 1}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    print("[Camera] Camera Module 3 ready ✅\n")

    # ── Startup greeting ──────────────────────────────────────────────────────
    threading.Thread(
        target=speak,
        args=("Safe and Healthy Chef is now active. All agents online.",),
        daemon=True
    ).start()

    # ── Launch all agents ─────────────────────────────────────────────────────
    agents = [
        ("GasAgent",        threading.Thread(target=agent_gas.run,                              daemon=True)),
        ("SafetyAgent",     threading.Thread(target=agent_safety.run,     args=(picam2,),       daemon=True)),
        ("IngredientAgent", threading.Thread(target=agent_ingredient.run, args=(picam2,),       daemon=True)),
        ("Orchestrator",    threading.Thread(target=agent_orchestrator.run,                     daemon=True)),
        ("LiveAgent",       threading.Thread(target=agent_live.run,       args=(picam2,),       daemon=True)),
        ("DHT11Agent",      threading.Thread(target=agent_dht11.run,                            daemon=True)),
        ("StorageAgent",    threading.Thread(target=agent_storage.run,    args=(picam2,),       daemon=True)),
    ]

    for name, t in agents:
        t.name = name
        t.start()
        print(f"[Main]  ✅ {name} started")

    # ── Live OpenCV display ───────────────────────────────────────────────────
    print("\n[Display] Live window started. Press 'q' to quit.\n")
    cv2.namedWindow("Safe & Healthy Chef", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Safe & Healthy Chef", *DISPLAY_RESOLUTION)

    try:
        while not stop_flag.is_set():
            frame = picam2.capture_array()
            if ROTATE_180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = draw_overlay(frame)
            cv2.imshow("Safe & Healthy Chef", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("\n[Display] Quitting ...")
                break

    except KeyboardInterrupt:
        print("\n[Display] Interrupted.")

    stop_flag.set()
    cv2.destroyAllWindows()
    time.sleep(1)
    picam2.stop()
    print("\n[Main]  All agents stopped. 👋")


if __name__ == "__main__":
    main()

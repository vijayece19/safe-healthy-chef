"""
config.py — Shared Configuration & Kitchen State
=================================================
All agents import from this file.
Single source of truth for settings and shared state.
"""

import threading
import os

# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL    = "gemini-2.5-flash"

# ─────────────────────────────────────────────────────────────────────────────
#  CAMERA
# ─────────────────────────────────────────────────────────────────────────────
DISPLAY_RESOLUTION  = (1280, 720)
CAPTURE_RESOLUTION  = (1920, 1080)
ROTATE_180          = True          # set False if camera is mounted correctly

# ─────────────────────────────────────────────────────────────────────────────
#  AGENT INTERVALS
# ─────────────────────────────────────────────────────────────────────────────
SAFETY_INTERVAL      = 60    # seconds between safety scans
INGREDIENT_INTERVAL  = 30    # seconds between ingredient scans
ORCHESTRATOR_INTERVAL= 90    # seconds between orchestrator runs
GAS_INTERVAL         = 2     # seconds between gas readings
DHT_INTERVAL         = 5     # seconds between DHT11 temperature/humidity reads

# ─────────────────────────────────────────────────────────────────────────────
#  HARDWARE
# ─────────────────────────────────────────────────────────────────────────────
GAS_PIN         = 4           # MQ2  DO   → GPIO 4  (physical pin 7)
DHT_PIN         = 17          # DHT11 DATA → GPIO 17 (physical pin 11)
                              # DHT11 VCC  → 5V      (physical pin 4)
                              # DHT11 GND  → GND     (physical pin 6)

# ─────────────────────────────────────────────────────────────────────────────
#  ALERTS
# ─────────────────────────────────────────────────────────────────────────────
ALERT_COOLDOWN  = 15          # seconds before same alert repeats

# ─────────────────────────────────────────────────────────────────────────────
#  SHARED KITCHEN STATE — all agents read/write here
# ─────────────────────────────────────────────────────────────────────────────
kitchen_state = {
    # Safety Agent
    "pan_present":      False,
    "hands_present":    False,
    "gloves_on":        None,
    "safety_alert":     False,
    "safety_notes":     "Waiting for scan ...",
    "safety_time":      "Never",
    "safety_conf":      "—",

    # Ingredient Agent
    "salt_level":       "unknown",
    "chilli_level":     "unknown",
    "oil_visible":      False,
    "vegetables":       [],
    "warnings":         [],
    "overall_status":   "unknown",
    "ingredient_notes": "Waiting for scan ...",
    "ingredient_time":  "Never",

    # Gas Agent
    "gas_detected":     False,

    # DHT11 Agent
    "temperature":      None,   # degrees Celsius
    "humidity":         None,   # percentage
    "dht_time":         "Never",

    # Orchestrator Agent
    "kitchen_status":   "unknown",
    "priority_alert":   "none",
    "chef_advice":      "Initialising ...",
    "priority_message": "",
}

# Thread-safe lock for kitchen_state
state_lock = threading.Lock()

# Global stop signal for all agents
stop_flag = threading.Event()

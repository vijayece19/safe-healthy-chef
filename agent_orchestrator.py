"""
agent_orchestrator.py — Orchestrator Agent
============================================
Reads reports from all 4 agents (Gas, Safety, Ingredient, DHT11).
Uses Gemini to prioritise alerts and advise the chef.
Runs every 90 seconds.

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import json
import re
import time
from datetime import datetime

from google import genai
from google.genai import types

from config import (
    kitchen_state, state_lock, stop_flag,
    GEMINI_API_KEY, GEMINI_MODEL,
    ORCHESTRATOR_INTERVAL
)

# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI SETUP
# ─────────────────────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

ORCHESTRATOR_PROMPT = """
You are a kitchen safety orchestrator coordinating 4 specialist agents.

You receive real-time reports from each agent:

Gas Agent Report:
{gas_report}

Safety Agent Report:
{safety_report}

Ingredient Agent Report:
{ingredient_report}

Environment Report (temperature & humidity from DHT11 sensor):
{environment_report}

Your responsibilities:
1. Identify the most critical issue across all agents
2. Prioritise: gas > safety > ingredient > environment
3. Generate a unified kitchen status
4. Give the chef one clear action to take right now
5. If temperature is above 35°C, note the kitchen is getting very hot
6. If humidity is above 80%, note condensation risk

Respond ONLY with valid JSON, no markdown fences:
{{
  "kitchen_status":  <"safe"|"warning"|"danger">,
  "priority_alert":  <"gas"|"safety"|"ingredient"|"environment"|"none">,
  "priority_message":"<the single most important alert in one sentence>",
  "all_clear":       <true|false>,
  "chef_advice":     "<one sentence — exactly what the chef should do now>"
}}
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR AGENT
# ─────────────────────────────────────────────────────────────────────────────
def run():
    """
    Orchestrator Agent main loop.
    Reads kitchen_state from all agents every ORCHESTRATOR_INTERVAL seconds.
    Sends summary to Gemini for unified decision making.
    """
    print("[Orchestrator] ✅ Started — coordinating every "
          f"{ORCHESTRATOR_INTERVAL}s\n")

    # Wait for other agents to get their first readings
    time.sleep(35)

    while not stop_flag.is_set():
        try:
            # ── Collect all agent reports ─────────────────────────────────────
            with state_lock:
                gas_report = {
                    "gas_detected": kitchen_state["gas_detected"],
                }
                safety_report = {
                    "pan_present":   kitchen_state["pan_present"],
                    "hands_present": kitchen_state["hands_present"],
                    "gloves_on":     kitchen_state["gloves_on"],
                    "safety_alert":  kitchen_state["safety_alert"],
                    "notes":         kitchen_state["safety_notes"],
                }
                ingredient_report = {
                    "salt_level":     kitchen_state["salt_level"],
                    "chilli_level":   kitchen_state["chilli_level"],
                    "oil_visible":    kitchen_state["oil_visible"],
                    "overall_status": kitchen_state["overall_status"],
                    "warnings":       kitchen_state["warnings"],
                }
                environment_report = {
                    "temperature_c": kitchen_state["temperature"],
                    "humidity_pct":  kitchen_state["humidity"],
                    "last_read":     kitchen_state["dht_time"],
                }

            # ── Build prompt ──────────────────────────────────────────────────
            prompt = ORCHESTRATOR_PROMPT.format(
                gas_report         = json.dumps(gas_report,         indent=2),
                safety_report      = json.dumps(safety_report,      indent=2),
                ingredient_report  = json.dumps(ingredient_report,  indent=2),
                environment_report = json.dumps(environment_report, indent=2),
            )

            # ── Call Gemini (text only — no image needed) ─────────────────────
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[prompt]
            )
            raw    = response.text.strip()
            raw    = re.sub(r"```(?:json)?", "", raw).strip()
            result = json.loads(raw)

            # ── Update shared state ───────────────────────────────────────────
            with state_lock:
                kitchen_state.update({
                    "kitchen_status":   result.get("kitchen_status",   "unknown"),
                    "priority_alert":   result.get("priority_alert",   "none"),
                    "priority_message": result.get("priority_message", ""),
                    "chef_advice":      result.get("chef_advice",      ""),
                })

            timestamp = datetime.now().strftime("%H:%M:%S")
            status    = result.get("kitchen_status",   "?")
            priority  = result.get("priority_alert",   "none")
            advice    = result.get("chef_advice",      "")
            message   = result.get("priority_message", "")
            icon      = "✅" if status == "safe" else "⚠️" if status == "warning" else "🚨"

            with state_lock:
                temp = kitchen_state["temperature"]
                hum  = kitchen_state["humidity"]

            temp_str = f"{temp}°C" if temp is not None else "—"
            hum_str  = f"{hum}%"   if hum  is not None else "—"

            print(
                f"[{timestamp}] [Orchestrator] {icon}"
                f"\n  Kitchen : {status.upper()}"
                f"\n  Priority: {priority.upper()}"
                f"\n  Env     : {temp_str}  {hum_str}"
                f"\n  📢 {message}"
                f"\n  👨‍🍳 {advice}\n"
            )

        except json.JSONDecodeError as e:
            print(f"[Orchestrator] ❌ JSON error: {e}")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                print("[Orchestrator] ⏳ Quota hit — waiting 60s ...")
                time.sleep(60)
            else:
                print(f"[Orchestrator] ❌ {error_str[:150]}")

        time.sleep(ORCHESTRATOR_INTERVAL)

    print("[Orchestrator] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN STANDALONE (for testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 45)
    print("  🎯  Orchestrator Agent — Standalone Test")
    print("=" * 45 + "\n")

    # Populate mock state for testing
    with state_lock:
        kitchen_state.update({
            "gas_detected":  False,
            "pan_present":   True,
            "hands_present": True,
            "gloves_on":     False,
            "safety_alert":  True,
            "salt_level":    "too_much",
            "chilli_level":  "moderate",
            "oil_visible":   False,
            "overall_status":"warning",
            "warnings":      ["too much salt"],
            "temperature":   32,
            "humidity":      65,
            "dht_time":      "12:00:00",
        })

    print("Running with mock state (pan+hands, no gloves, too much salt, 32°C 65%)...\n")
    print("Press Ctrl+C to stop.\n")
    try:
        run()
    except KeyboardInterrupt:
        stop_flag.set()
        print("\n[Orchestrator] Stopped by user.")

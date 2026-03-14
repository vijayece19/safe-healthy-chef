"""
agent_dht11.py — DHT11 Temperature & Humidity Agent
=====================================================
Reads DHT11 sensor every DHT_INTERVAL seconds via lgpio bit-bang.
Updates shared kitchen_state with temperature and humidity.

Wiring:
  DHT11 VCC  → Physical pin 4  (5V)
  DHT11 GND  → Physical pin 6  (GND)
  DHT11 DATA → Physical pin 11 (GPIO 17)

Note: DHT11 frequently returns bad reads — this is normal.
  The agent simply retries on the next cycle.
  Recommend a 10kΩ pull-up resistor between VCC and DATA.

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import lgpio
import time
from datetime import datetime

from config import (
    kitchen_state, state_lock, stop_flag,
    DHT_PIN, DHT_INTERVAL
)

# ─────────────────────────────────────────────────────────────────────────────
#  TIMING CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
LOOP_TIMEOUT  = 0.001    # 1 ms max wait per edge poll (DHT11 bits are ~26–70 µs)
BIT_THRESHOLD = 0.00005  # 50 µs threshold: longer = bit 1, shorter = bit 0


# ─────────────────────────────────────────────────────────────────────────────
#  DHT11 BIT-BANG READ
# ─────────────────────────────────────────────────────────────────────────────
def read_dht11(handle) -> tuple:
    """
    Bit-bang the DHT11 protocol using lgpio.
    Returns (temperature_C, humidity_pct) or (None, None) on any failure.
    Every polling loop is guarded by a deadline so this never hangs.
    """

    # ── 1. Send start signal: pull LOW for 20 ms, then release ───────────────
    lgpio.gpio_claim_output(handle, DHT_PIN, 0)
    time.sleep(0.02)
    lgpio.gpio_claim_input(handle, DHT_PIN)

    # ── 2. Wait for DHT11 to pull line LOW (sensor response start) ────────────
    deadline = time.time() + 0.1
    while lgpio.gpio_read(handle, DHT_PIN) == 1:
        if time.time() > deadline:
            return None, None

    # ── 3. Wait for response LOW pulse to finish (~80 µs) ────────────────────
    deadline = time.time() + LOOP_TIMEOUT
    while lgpio.gpio_read(handle, DHT_PIN) == 0:
        if time.time() > deadline:
            return None, None

    # ── 4. Wait for response HIGH pulse to finish (~80 µs) ───────────────────
    deadline = time.time() + LOOP_TIMEOUT
    while lgpio.gpio_read(handle, DHT_PIN) == 1:
        if time.time() > deadline:
            return None, None

    # ── 5. Read 40 data bits ─────────────────────────────────────────────────
    bit_times = []
    for _ in range(40):
        # Each bit begins with a ~50 µs LOW pulse
        deadline = time.time() + LOOP_TIMEOUT
        while lgpio.gpio_read(handle, DHT_PIN) == 0:
            if time.time() > deadline:
                return None, None

        # Followed by a HIGH pulse: ~26 µs = bit 0, ~70 µs = bit 1
        start    = time.time()
        deadline = start + LOOP_TIMEOUT
        while lgpio.gpio_read(handle, DHT_PIN) == 1:
            if time.time() > deadline:
                return None, None
        bit_times.append(time.time() - start)

    # ── 6. Decode bit times into 0s and 1s ───────────────────────────────────
    bits = [1 if t > BIT_THRESHOLD else 0 for t in bit_times]

    # ── 7. Pack 40 bits into 5 bytes ─────────────────────────────────────────
    bytes_ = []
    for i in range(5):
        byte = 0
        for bit in bits[i * 8 : (i + 1) * 8]:
            byte = (byte << 1) | bit
        bytes_.append(byte)

    # Byte layout: [hum_int, hum_dec, temp_int, temp_dec, checksum]
    hum_int,  hum_dec  = bytes_[0], bytes_[1]
    temp_int, temp_dec = bytes_[2], bytes_[3]
    checksum           = bytes_[4]

    # ── 8. Verify checksum ────────────────────────────────────────────────────
    expected = (hum_int + hum_dec + temp_int + temp_dec) & 0xFF
    if checksum != expected:
        return None, None

    return temp_int, hum_int


# ─────────────────────────────────────────────────────────────────────────────
#  DHT11 AGENT
# ─────────────────────────────────────────────────────────────────────────────
def run():
    """
    DHT11 Agent main loop.
    Reads temperature and humidity every DHT_INTERVAL seconds.
    Bad reads are skipped silently — DHT11 has ~10–15 % error rate normally.
    """
    handle = lgpio.gpiochip_open(0)
    print(f"[DHT11 Agent] ✅ Started — reading every {DHT_INTERVAL}s on GPIO {DHT_PIN}\n")

    while not stop_flag.is_set():
        try:
            temp, hum = read_dht11(handle)
            timestamp = datetime.now().strftime("%H:%M:%S")

            if temp is not None:
                with state_lock:
                    kitchen_state["temperature"] = temp
                    kitchen_state["humidity"]    = hum
                    kitchen_state["dht_time"]    = timestamp
                print(f"[{timestamp}] [DHT11 Agent] 🌡️  {temp}°C   💧 {hum}%")
            else:
                print(f"[{timestamp}] [DHT11 Agent] ⚠️  Bad read — will retry in {DHT_INTERVAL}s")

        except Exception as e:
            print(f"[DHT11 Agent] ❌ Error: {e}")

        time.sleep(DHT_INTERVAL)

    lgpio.gpiochip_close(handle)
    print("[DHT11 Agent] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN STANDALONE (for testing)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 45)
    print("  🌡️   DHT11 Agent — Standalone Test")
    print("=" * 45 + "\n")
    print("Press Ctrl+C to stop.\n")
    try:
        run()
    except KeyboardInterrupt:
        stop_flag.set()
        print("\n[DHT11 Agent] Stopped by user.")

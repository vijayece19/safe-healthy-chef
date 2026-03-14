"""
test_dht.py — DHT11 Basic Sensor Check
========================================
Wiring (as planned):
  DHT11 VCC  → Physical pin 4  (5V)
  DHT11 GND  → Physical pin 6  (GND)
  DHT11 DATA → Physical pin 11 (GPIO 17)  ← fixed from GPIO 4

Run:
  python3 test_dht.py

Expects at least 1 good reading out of MAX_TRIES attempts.
DHT11 is a slow sensor — bad reads are normal, hence retries.
"""

import lgpio
import time

# ── Config ────────────────────────────────────────────────────────────────────
GPIO_PIN  = 17          # DATA on physical pin 11 — NOT pin 4 (that's your MQ2)
MAX_TRIES = 10          # DHT11 fails often; 10 tries gives a fair sample
DELAY     = 2.5         # DHT11 needs ≥2 s between reads (spec limit)

# ── Timing thresholds (seconds) ───────────────────────────────────────────────
LOOP_TIMEOUT = 0.001    # 1 ms max wait per edge (DHT11 bits are ~26–70 µs)
BIT_THRESHOLD = 0.00005 # 50 µs: pulses longer than this = bit 1, shorter = bit 0


# ─────────────────────────────────────────────────────────────────────────────
def read_dht11(handle) -> tuple[int | None, int | None]:
    """
    Bit-bang DHT11 protocol via lgpio.
    Returns (temperature_C, humidity_pct) or (None, None) on failure.
    All polling loops are guarded by a timeout so the script never hangs.
    """

    # ── 1. Send start signal: pull LOW for 20 ms, then release ───────────────
    lgpio.gpio_claim_output(handle, GPIO_PIN, 0)
    time.sleep(0.02)
    lgpio.gpio_claim_input(handle, GPIO_PIN)

    # ── 2. Wait for DHT11 to pull the line LOW (response start) ──────────────
    deadline = time.time() + 0.1          # 100 ms window
    while lgpio.gpio_read(handle, GPIO_PIN) == 1:
        if time.time() > deadline:
            return None, None             # sensor never responded

    # ── 3. Wait for response LOW to finish (~80 µs) ───────────────────────────
    deadline = time.time() + LOOP_TIMEOUT
    while lgpio.gpio_read(handle, GPIO_PIN) == 0:
        if time.time() > deadline:
            return None, None

    # ── 4. Wait for response HIGH to finish (~80 µs) ─────────────────────────
    deadline = time.time() + LOOP_TIMEOUT
    while lgpio.gpio_read(handle, GPIO_PIN) == 1:
        if time.time() > deadline:
            return None, None

    # ── 5. Read 40 bits ───────────────────────────────────────────────────────
    bit_times = []
    for _ in range(40):
        # Each bit starts with a ~50 µs LOW pulse
        deadline = time.time() + LOOP_TIMEOUT
        while lgpio.gpio_read(handle, GPIO_PIN) == 0:
            if time.time() > deadline:
                return None, None         # stuck LOW — abort

        # Then a HIGH pulse whose length encodes 0 (~26 µs) or 1 (~70 µs)
        start    = time.time()
        deadline = start + LOOP_TIMEOUT
        while lgpio.gpio_read(handle, GPIO_PIN) == 1:
            if time.time() > deadline:
                return None, None         # stuck HIGH — abort
        bit_times.append(time.time() - start)

    # ── 6. Decode bits ────────────────────────────────────────────────────────
    bits = [1 if t > BIT_THRESHOLD else 0 for t in bit_times]

    # ── 7. Pack bits into 5 bytes ─────────────────────────────────────────────
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
        return None, None                 # corrupted frame

    return temp_int, hum_int


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 45)
    print("  🌡️   DHT11 Sensor — Basic Check")
    print("=" * 45)
    print(f"  GPIO : {GPIO_PIN} (physical pin 11)")
    print(f"  Tries: {MAX_TRIES}  |  Interval: {DELAY}s")
    print("=" * 45 + "\n")

    handle = lgpio.gpiochip_open(0)

    good_reads  = 0
    temps, hums = [], []

    try:
        for attempt in range(1, MAX_TRIES + 1):
            temp, hum = read_dht11(handle)

            if temp is not None:
                good_reads += 1
                temps.append(temp)
                hums.append(hum)
                print(f"  [{attempt:02d}/{MAX_TRIES}] ✅  Temp: {temp}°C   "
                      f"Humidity: {hum}%")
            else:
                print(f"  [{attempt:02d}/{MAX_TRIES}] ❌  Bad read — "
                      f"retrying in {DELAY}s ...")

            if attempt < MAX_TRIES:
                time.sleep(DELAY)

    except KeyboardInterrupt:
        print("\n  Stopped by user.")

    finally:
        lgpio.gpiochip_close(handle)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 45)
    print("  Summary")
    print("=" * 45)
    print(f"  Good reads : {good_reads} / {MAX_TRIES}")

    if good_reads > 0:
        print(f"  Temp  avg  : {sum(temps)/len(temps):.1f}°C  "
              f"(min {min(temps)}°C / max {max(temps)}°C)")
        print(f"  Humidity avg: {sum(hums)/len(hums):.1f}%  "
              f"(min {min(hums)}% / max {max(hums)}%)")
        print("\n  ✅ Sensor is working correctly.\n")
    else:
        print("\n  ❌ No valid readings received.")
        print("  Check:")
        print("    1. DATA wire is on physical pin 11 (GPIO 17)")
        print("    2. VCC is on physical pin 4 (5V), not 3.3V")
        print("    3. GND is on physical pin 6")
        print("    4. 10kΩ pull-up resistor between VCC and DATA\n")


if __name__ == "__main__":
    main()

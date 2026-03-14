# Safe & Healthy Chef — Day 01 & 02 Setup Guide

## 1. OS Prerequisites (run once on your Pi 5)

```bash
sudo apt update && sudo apt upgrade -y

# Camera support (picamera2)
sudo apt install -y python3-picamera2 python3-libcamera python3-kms++ libcap-dev

# GPIO / DHT support
sudo apt install -y libgpiod2 python3-dev

# Audio output for pyttsx3
sudo apt install -y espeak espeak-ng libespeak-ng-dev ffmpeg
```

---

## 2. Python Packages

```bash
pip install \
  adafruit-circuitpython-dht \
  picamera2 \
  google-generativeai \
  pyttsx3 \
  Pillow \
  RPi.GPIO
```

> **Note:** If `picamera2` is already installed system-wide via `apt`, skip it from pip.

---

## 3. Enable Camera Interface

```bash
sudo raspi-config
# → Interface Options → Camera → Enable
sudo reboot
```

Test camera works:
```bash
rpicam-still -o test.jpg
```

---

## 4. Gemini API Key

Get a free key at → https://aistudio.google.com/app/apikey

```bash
# Add to your shell profile (~/.bashrc or ~/.zshrc):
export GEMINI_API_KEY="your_key_here"
source ~/.bashrc
```

---

## 5. Wiring (DHT11 module — no resistors needed)

| DHT11 Pin | Pi 5 Pin         | Function    |
|-----------|------------------|-------------|
| VCC       | Pin 1  (3.3V)    | Power       |
| Data      | Pin 7  (GPIO 4)  | Sensor Data |
| GND       | Pin 9  (GND)     | Ground      |

---

## 6. Run Day 01

```bash
python3 day01_kitchen_vitals.py
```

**Controls:**
- `SPACE` → capture a full-res photo (saved to `kitchen_photos/`)
- `q` / `Ctrl+C` → quit

**Expected output:**
```
[08:32:10] 🌡  Temp: 27.0°C  💧 Humidity: 55.0%
[08:32:15] 🌡  Temp: 27.0°C  💧 Humidity: 55.0%
[Camera] 📸 Photo saved → kitchen_photos/kitchen_20250601_083217.jpg
```

---

## 7. Run Day 02

```bash
python3 day02_safety_agent.py
```

**What it does every 4 seconds:**
1. Captures a 1080p frame
2. Sends it to Gemini 2.0 Flash with the safety prompt
3. Parses the JSON response
4. If pan + bare hands are detected → speaks the glove alert aloud
5. DHT11 still logs temperature/humidity in parallel

**Alert cooldown** is 10 seconds by default — tweak `ALERT_COOLDOWN` to change.

---

## 8. Troubleshooting

| Problem | Fix |
|---------|-----|
| `RuntimeError: DHT sensor not found` | Check wiring, try `use_pulseio=True` |
| `ModuleNotFoundError: picamera2` | `sudo apt install python3-picamera2` |
| Gemini returns non-JSON | The regex in `analyze_frame()` strips fences; if it still fails, check your API key |
| No sound from pyttsx3 | `sudo apt install espeak` and ensure audio output is not muted |
| Camera black frame | Add `time.sleep(2)` after `picam2.start()` (already in the scripts) |

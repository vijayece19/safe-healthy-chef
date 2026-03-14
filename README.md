

# 🍳 Safe & Healthy Chef

**Multi-Agent AI Kitchen Safety System — Google Gemini Live Agent Challenge 2026**

## 🎯 What It Does
Real-time kitchen safety monitoring using Raspberry Pi 5 + Gemini AI:
- 🧤 Detects pan handling without safety gloves
- 🧂 Monitors salt/chilli/oil levels in No Oil No Boil salad
- 🔥 Gas leakage detection via MQ2 sensor
- 🌡️ Temperature & humidity monitoring via DHT11
- 🎙️ Chef AI — speak questions, get instant voice responses
- ☁️ All alerts uploaded to Google Cloud Storage

## 🤖 Multi-Agent Architecture
| Agent | Role |
|-------|------|
| agent_gas.py | MQ2 gas monitoring every 2s |
| agent_safety.py | Pan + gloves via Gemini Vision |
| agent_ingredient.py | Salt/chilli/oil analysis |
| agent_orchestrator.py | Coordinates all agents |
| agent_live.py | Chef AI real-time voice |
| agent_dht11.py | Temperature & humidity |
| agent_storage.py | Google Cloud Storage |

## 🛠️ Hardware
- Raspberry Pi 5 (16GB)
- Camera Module 3 (CSI)
- MQ2 Gas Sensor (GPIO 4)
- DHT11 Sensor (GPIO 17)
- USB Microphone + Speaker

## ☁️ Google Cloud Services
- **Google Cloud Storage** — alert photos + JSON logs
- **Gemini 2.5 Flash** — vision + reasoning
- **Gemini Native Audio** — real-time voice

## 🚀 Setup & Run
```bash
# Install dependencies
pip install google-genai google-cloud-storage picamera2
            pyttsx3 lgpio Pillow opencv-python pyaudio

# Set API key
export GEMINI_API_KEY="your_key_here"

# Authenticate GCS
gcloud auth application-default login

# Run
python3 main.py
```

## 📁 Project Structure
```
├── config.py              # Shared state
├── agent_gas.py           # Gas Agent
├── agent_safety.py        # Safety Agent  
├── agent_ingredient.py    # Ingredient Agent
├── agent_orchestrator.py  # Orchestrator
├── agent_live.py          # Chef AI Live
├── agent_dht11.py         # DHT11 Agent
├── agent_storage.py       # Cloud Storage
└── main.py                # Run this
```

## 🏆 Built for
Google Gemini Live Agent Challenge 2026
#GeminiLiveAgentChallenge

"""
agent_storage.py — Google Cloud Storage Agent
===============================================
Uploads kitchen alert photos and JSON logs to GCS.
Uses Application Default Credentials (gcloud auth).

What it uploads:
  • Alert photos when safety/gas/ingredient violations detected
  • JSON log of all kitchen events every 5 minutes
  • Startup snapshot to confirm system is running

Bucket structure:
  safe-healthy-chef-pi/
  ├── alerts/
  │   ├── safety_20250317_093045.jpg
  │   ├── ingredient_20250317_093120.jpg
  │   └── gas_20250317_093200.jpg
  ├── logs/
  │   └── kitchen_log_20250317.json
  └── snapshots/
      └── startup_20250317_090000.jpg

Setup:
  gcloud auth application-default login
  gcloud auth application-default set-quota-project safe-healthy-chef
  pip install google-cloud-storage

Part of: Safe & Healthy Chef — Multi-Agent System
"""

import io
import json
import os
import time
import threading
from datetime import datetime

from google.cloud import storage
from PIL import Image

from config import (
    kitchen_state, state_lock, stop_flag,
    ROTATE_180
)

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
GCS_BUCKET_NAME    = "safe-healthy-chef-pi"
LOG_INTERVAL       = 300    # upload full JSON log every 5 minutes
ALERT_COOLDOWN_GCS = 30     # min seconds between uploads for same alert type


# ─────────────────────────────────────────────────────────────────────────────
#  GLOBALS
# ─────────────────────────────────────────────────────────────────────────────
_last_upload = {
    "safety":     0,
    "ingredient": 0,
    "gas":        0,
}
_upload_lock = threading.Lock()
_event_log   = []
_log_lock    = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
#  GCS CLIENT — Application Default Credentials
# ─────────────────────────────────────────────────────────────────────────────
def get_client() -> storage.Client:
    """Return GCS client using Application Default Credentials (gcloud auth)."""
    return storage.Client(project="safe-healthy-chef")


def get_bucket() -> storage.Bucket:
    return get_client().bucket(GCS_BUCKET_NAME)


# ─────────────────────────────────────────────────────────────────────────────
#  UPLOAD HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def upload_image(pil_image: Image.Image, blob_path: str) -> str | None:
    """Upload PIL image as JPEG to GCS. Returns URL or None."""
    try:
        buf = io.BytesIO()
        pil_image.convert("RGB").save(buf, format="JPEG", quality=85)
        buf.seek(0)

        bucket = get_bucket()
        blob   = bucket.blob(blob_path)
        blob.upload_from_file(buf, content_type="image/jpeg")

        url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{blob_path}"
        print(f"[Storage] ☁️  Image uploaded → {blob_path}")
        return url

    except Exception as e:
        print(f"[Storage] ❌ Image upload failed: {e}")
        return None


def upload_json(data: dict, blob_path: str) -> bool:
    """Upload dict as JSON to GCS. Returns True on success."""
    try:
        bucket  = get_bucket()
        blob    = bucket.blob(blob_path)
        content = json.dumps(data, indent=2, default=str)
        blob.upload_from_string(content, content_type="application/json")
        print(f"[Storage] ☁️  JSON uploaded  → {blob_path}")
        return True

    except Exception as e:
        print(f"[Storage] ❌ JSON upload failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  LOG EVENT
# ─────────────────────────────────────────────────────────────────────────────
def log_event(event_type: str, details: dict):
    """Add event to in-memory log."""
    with _log_lock:
        _event_log.append({
            "timestamp":  datetime.now().isoformat(),
            "event_type": event_type,
            "details":    details,
        })


# ─────────────────────────────────────────────────────────────────────────────
#  ALERT UPLOAD
# ─────────────────────────────────────────────────────────────────────────────
def upload_alert(alert_type: str, picam2, details: dict):
    """Capture photo and upload to GCS with alert metadata."""
    global _last_upload

    now = time.time()
    with _upload_lock:
        if now - _last_upload.get(alert_type, 0) < ALERT_COOLDOWN_GCS:
            return
        _last_upload[alert_type] = now

    try:
        frame   = picam2.capture_array()
        pil_img = Image.fromarray(frame)
        if ROTATE_180:
            pil_img = pil_img.rotate(180)

        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path  = f"alerts/{alert_type}_{ts}.jpg"
        meta_path = f"alerts/{alert_type}_{ts}.json"

        img_url = upload_image(pil_img, img_path)

        meta = {
            "alert_type": alert_type,
            "timestamp":  datetime.now().isoformat(),
            "image_url":  img_url,
            "details":    details,
        }
        upload_json(meta, meta_path)
        log_event(f"alert_{alert_type}", meta)

        print(f"[Storage] 🚨 Alert uploaded: {alert_type} @ {ts}")

    except Exception as e:
        print(f"[Storage] ❌ Alert upload error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  STARTUP SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────
def upload_startup_snapshot(picam2):
    """Upload startup photo to confirm system is live on GCS."""
    try:
        frame   = picam2.capture_array()
        pil_img = Image.fromarray(frame)
        if ROTATE_180:
            pil_img = pil_img.rotate(180)

        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"snapshots/startup_{ts}.jpg"
        url  = upload_image(pil_img, path)

        info = {
            "event":     "system_startup",
            "timestamp": datetime.now().isoformat(),
            "snapshot":  url,
            "agents": [
                "agent_gas",
                "agent_safety",
                "agent_ingredient",
                "agent_orchestrator",
                "agent_live",
                "agent_dht11",
                "agent_storage",
            ],
            "model":    "gemini-2.5-flash + gemini-2.5-flash-native-audio",
            "hardware": "Raspberry Pi 5 + Camera Module 3 + MQ2 + DHT11",
            "bucket":   GCS_BUCKET_NAME,
        }
        upload_json(info, f"snapshots/startup_{ts}.json")
        log_event("system_startup", info)
        print(f"[Storage] ✅ Startup snapshot uploaded")

    except Exception as e:
        print(f"[Storage] ❌ Startup snapshot error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  PERIODIC LOG UPLOADER
# ─────────────────────────────────────────────────────────────────────────────
def log_upload_loop():
    """Upload kitchen state + event log to GCS every LOG_INTERVAL seconds."""
    time.sleep(60)

    while not stop_flag.is_set():
        try:
            with state_lock:
                state_snapshot = kitchen_state.copy()
            with _log_lock:
                events_copy = list(_event_log)

            log_data = {
                "timestamp":     datetime.now().isoformat(),
                "kitchen_state": state_snapshot,
                "events":        events_copy,
                "total_events":  len(events_copy),
            }

            date_str = datetime.now().strftime("%Y%m%d")
            upload_json(log_data, f"logs/kitchen_log_{date_str}.json")

        except Exception as e:
            print(f"[Storage] ❌ Log upload error: {e}")

        time.sleep(LOG_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
#  ALERT WATCHER
# ─────────────────────────────────────────────────────────────────────────────
def alert_watcher_loop(picam2):
    """Watches kitchen_state and uploads alert photos on violations."""
    print("[Storage] 👁️  Alert watcher started\n")

    prev_safety     = False
    prev_gas        = False
    prev_salt       = ""
    prev_oil        = False

    while not stop_flag.is_set():
        try:
            with state_lock:
                safety_alert = kitchen_state.get("safety_alert",  False)
                gas_detected = kitchen_state.get("gas_detected",  False)
                salt_level   = kitchen_state.get("salt_level",    "unknown")
                oil_visible  = kitchen_state.get("oil_visible",   False)
                pan          = kitchen_state.get("pan_present",   False)
                hands        = kitchen_state.get("hands_present", False)
                gloves       = kitchen_state.get("gloves_on",     None)

            if safety_alert and not prev_safety:
                threading.Thread(
                    target=upload_alert,
                    args=("safety", picam2, {
                        "pan_present":   pan,
                        "hands_present": hands,
                        "gloves_on":     gloves,
                    }),
                    daemon=True
                ).start()
                log_event("safety_alert", {
                    "pan": pan, "hands": hands, "gloves": gloves
                })

            if gas_detected and not prev_gas:
                threading.Thread(
                    target=upload_alert,
                    args=("gas", picam2, {"gas_detected": True}),
                    daemon=True
                ).start()
                log_event("gas_alert", {"gas_detected": True})

            ingredient_alert = (salt_level == "too_much" or oil_visible)
            prev_ingredient  = (prev_salt == "too_much" or prev_oil)

            if ingredient_alert and not prev_ingredient:
                threading.Thread(
                    target=upload_alert,
                    args=("ingredient", picam2, {
                        "salt_level":  salt_level,
                        "oil_visible": oil_visible,
                    }),
                    daemon=True
                ).start()
                log_event("ingredient_alert", {
                    "salt": salt_level, "oil": oil_visible
                })

            prev_safety = safety_alert
            prev_gas    = gas_detected
            prev_salt   = salt_level
            prev_oil    = oil_visible

        except Exception as e:
            print(f"[Storage] ❌ Watcher error: {e}")

        time.sleep(3)

    print("[Storage] Alert watcher stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  RUN (called from main.py)
# ─────────────────────────────────────────────────────────────────────────────
def run(picam2):
    """Entry point called by main.py as daemon thread."""
    print("[Storage] ✅ Started — Google Cloud Storage agent")
    print(f"[Storage] 🪣  Bucket: {GCS_BUCKET_NAME}")
    print("[Storage] 🔑  Using Application Default Credentials\n")

    threading.Thread(
        target=upload_startup_snapshot,
        args=(picam2,),
        daemon=True
    ).start()

    threading.Thread(
        target=alert_watcher_loop,
        args=(picam2,),
        daemon=True,
        name="StorageWatcher"
    ).start()

    threading.Thread(
        target=log_upload_loop,
        daemon=True,
        name="StorageLogger"
    ).start()

    print("[Storage] ✅ All storage threads running\n")


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  ☁️   Cloud Storage Agent — Connection Test")
    print("=" * 50 + "\n")

    print(f"[Test] Bucket : {GCS_BUCKET_NAME}")
    print("[Test] Auth   : Application Default Credentials")
    print("[Test] Uploading test JSON ...\n")

    test_data = {
        "test":      True,
        "timestamp": datetime.now().isoformat(),
        "message":   "Safe & Healthy Chef — GCS connection test ✅",
        "project":   "safe-healthy-chef",
        "hardware":  "Raspberry Pi 5",
    }

    success = upload_json(test_data, "test/connection_test.json")

    if success:
        print("\n✅ GCS is working!")
        print(f"   Check your bucket: https://console.cloud.google.com/storage/browser/{GCS_BUCKET_NAME}")
    else:
        print("\n❌ Upload failed.")
        print("   Run: gcloud auth application-default login")
        print("   Then: gcloud auth application-default set-quota-project safe-healthy-chef")

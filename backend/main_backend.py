from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import subprocess
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pymavlink import mavutil

try:
    from picamera2 import Picamera2  # Raspberry Pi OS package, optional on laptops
except Exception:
    Picamera2 = None

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
FLIGHTS_DIR = DATA_DIR / "flights"
DB_PATH = DATA_DIR / "drone.db"

MAVLINK_CONNECTION = os.getenv("MAVLINK_CONNECTION", "/dev/serial0")
MAVLINK_BAUD = int(os.getenv("MAVLINK_BAUD", "115200"))
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN", "dev-only-change-me")
HOME_SSID = os.getenv("HOME_SSID", "")
CONTROL_LINK_TIMEOUT_S = float(os.getenv("CONTROL_LINK_TIMEOUT_S", "2.0"))
TAKEOFF_ALT_M = float(os.getenv("TAKEOFF_ALT_M", "5.0"))

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

DATA_DIR.mkdir(parents=True, exist_ok=True)
FLIGHTS_DIR.mkdir(parents=True, exist_ok=True)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def _init_db(self):
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS system_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def add_telemetry(self, payload: Dict[str, Any]):
        with self._connect() as con:
            con.execute(
                "INSERT INTO telemetry(ts, payload) VALUES (?, ?)",
                (utc_iso(), json.dumps(payload, separators=(",", ":"))),
            )

    def add_log(self, level: str, message: str):
        with self._connect() as con:
            con.execute(
                "INSERT INTO system_log(ts, level, message) VALUES (?, ?, ?)",
                (utc_iso(), level, message),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        with self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self._connect() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def latest_telemetry_id(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(MAX(id), 0) FROM telemetry").fetchone()
        return int(row[0] if row else 0)


store = LocalStore(DB_PATH)


class MavlinkBridge:
    """Single-owner MAVLink reader + serialized MAVLink writer."""

    def __init__(self):
        self.master = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.connected = False
        self.last_msg_monotonic = 0.0
        self.logs = deque(maxlen=150)
        self.last_command_ack: Optional[dict] = None

        self.state: Dict[str, Any] = {
            "armed": False,
            "mode": "UNKNOWN",
            "battery_pct": None,
            "voltage_v": None,
            "current_a": None,
            "gps_sats": None,
            "gps_fix": None,
            "lat": None,
            "lon": None,
            "alt_m": None,
            "relative_alt_m": None,
            "groundspeed_mps": None,
            "heading_deg": None,
            "roll_deg": None,
            "pitch_deg": None,
            "yaw_deg": None,
            "landed_state": None,
            "system_status": None,
        }

        # Filled by your ML pipeline; None means "not yet computed".
        self.crop_health: Dict[str, Dict[str, Optional[float]]] = {
            crop: {"healthy": None, "wilting": None, "disease": None, "pest": None}
            for crop in ("gabi", "saging", "palay")
        }

    def log(self, message: str, level: str = "INFO"):
        item = {"ts": utc_iso(), "level": level, "message": message}
        self.logs.append(item)
        store.add_log(level, message)
        print(f"[{level}] {message}")

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="mavlink-reader")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _connect(self):
        self.log(f"Opening MAVLink on {MAVLINK_CONNECTION} @ {MAVLINK_BAUD}")
        self.master = mavutil.mavlink_connection(
            MAVLINK_CONNECTION,
            baud=MAVLINK_BAUD,
            source_system=255,
            autoreconnect=True,
        )
        hb = self.master.wait_heartbeat(timeout=12)
        if hb is None:
            raise TimeoutError("No flight-controller heartbeat received")
        self.connected = True
        self.log(
            f"MAVLink heartbeat received: system={self.master.target_system}, "
            f"component={self.master.target_component}"
        )

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.master is None:
                    self._connect()
                msg = self.master.recv_match(blocking=True, timeout=1.0)
                if msg is None:
                    if time.monotonic() - self.last_msg_monotonic > 3.0:
                        self.connected = False
                    continue
                self.last_msg_monotonic = time.monotonic()
                self.connected = True
                self._handle_msg(msg)
            except Exception as exc:
                self.connected = False
                self.log(f"MAVLink reconnecting after error: {exc}", "WARN")
                try:
                    if self.master:
                        self.master.close()
                except Exception:
                    pass
                self.master = None
                time.sleep(2)

    def _handle_msg(self, msg):
        t = msg.get_type()
        if t == "BAD_DATA":
            return

        with self.state_lock:
            if t == "HEARTBEAT":
                self.state["armed"] = bool(
                    msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )
                try:
                    self.state["mode"] = mavutil.mode_string_v10(msg)
                except Exception:
                    self.state["mode"] = "UNKNOWN"
                self.state["system_status"] = int(msg.system_status)

            elif t == "SYS_STATUS":
                self.state["battery_pct"] = None if msg.battery_remaining < 0 else int(msg.battery_remaining)
                self.state["voltage_v"] = None if msg.voltage_battery == 65535 else round(msg.voltage_battery / 1000.0, 2)
                self.state["current_a"] = None if msg.current_battery == -1 else round(msg.current_battery / 100.0, 2)

            elif t == "GPS_RAW_INT":
                self.state["gps_sats"] = int(msg.satellites_visible)
                self.state["gps_fix"] = int(msg.fix_type)

            elif t == "GLOBAL_POSITION_INT":
                self.state["lat"] = msg.lat / 1e7
                self.state["lon"] = msg.lon / 1e7
                self.state["alt_m"] = round(msg.alt / 1000.0, 2)
                self.state["relative_alt_m"] = round(msg.relative_alt / 1000.0, 2)
                self.state["heading_deg"] = None if msg.hdg == 65535 else round(msg.hdg / 100.0, 1)

            elif t == "VFR_HUD":
                self.state["groundspeed_mps"] = round(float(msg.groundspeed), 2)

            elif t == "ATTITUDE":
                rad2deg = 57.29577951308232
                self.state["roll_deg"] = round(msg.roll * rad2deg, 1)
                self.state["pitch_deg"] = round(msg.pitch * rad2deg, 1)
                self.state["yaw_deg"] = round(msg.yaw * rad2deg, 1)

            elif t == "EXTENDED_SYS_STATE":
                self.state["landed_state"] = int(msg.landed_state)

            elif t == "COMMAND_ACK":
                self.last_command_ack = {
                    "command": int(msg.command),
                    "result": int(msg.result),
                    "ts": utc_iso(),
                }
                self.log(f"COMMAND_ACK command={msg.command} result={msg.result}")

    def snapshot(self) -> Dict[str, Any]:
        with self.state_lock:
            state = dict(self.state)
            analytics = json.loads(json.dumps(self.crop_health))
        return {
            "type": "telemetry",
            "ts": utc_iso(),
            "mavlink_connected": self.connected,
            "telemetry": state,
            "crop_health": analytics,
            "logs": list(self.logs)[-30:],
            "last_command_ack": self.last_command_ack,
        }

    def _require_link(self):
        if self.master is None or not self.connected:
            raise RuntimeError("Flight controller is not connected over MAVLink")

    def _send_command_long(self, command: int, params=None):
        self._require_link()
        p = list(params or []) + [0.0] * 7
        with self.write_lock:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                command,
                0,
                *p[:7],
            )

    def arm(self):
        snap = self.snapshot()["telemetry"]
        battery = snap.get("battery_pct")
        fix = snap.get("gps_fix")
        if battery is not None and battery < 20:
            raise RuntimeError("ARM blocked: battery below 20%")
        if fix is not None and fix < 3:
            raise RuntimeError("ARM blocked: GPS does not have a 3D fix")
        self._send_command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            [1.0, 0.0],
        )
        self.log("ARM command sent")

    def disarm(self):
        self._send_command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            [0.0, 0.0],
        )
        self.log("DISARM command sent")

    def set_mode(self, mode: str):
        self._require_link()
        mode = mode.upper()
        mapping = self.master.mode_mapping() or {}
        if mode not in mapping:
            raise RuntimeError(f"Mode {mode} not available. FC reports: {sorted(mapping)}")
        mode_id = mapping[mode]
        with self.write_lock:
            self.master.mav.set_mode_send(
                self.master.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mode_id,
            )
        self.log(f"SET_MODE {mode} sent")

    def takeoff(self, altitude_m: float = TAKEOFF_ALT_M):
        snap = self.snapshot()["telemetry"]
        if not snap.get("armed"):
            raise RuntimeError("TAKEOFF blocked: vehicle is not armed")
        self.set_mode("GUIDED")
        time.sleep(0.35)
        self._send_command_long(
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            [0, 0, 0, 0, 0, 0, float(altitude_m)],
        )
        self.log(f"TAKEOFF {altitude_m:.1f} m sent")

    def rtl(self):
        self.set_mode("RTL")

    def land(self):
        self.set_mode("LAND")

    def emergency_stop(self):
        """
        Safety policy:
        - If on ground, disarm.
        - If airborne/unknown, DO NOT force-kill motors; command LAND instead.
        """
        landed = self.snapshot()["telemetry"].get("landed_state")
        on_ground = landed == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
        if on_ground:
            self.disarm()
            self.log("EMERGENCY STOP: on-ground disarm requested", "WARN")
            return "disarm"
        self.land()
        self.log("EMERGENCY STOP: airborne motor-kill blocked; LAND requested", "WARN")
        return "land"

    def manual_control(self, x: int, y: int, z: int, r: int):
        self._require_link()
        x = max(-1000, min(1000, int(x)))
        y = max(-1000, min(1000, int(y)))
        z = max(0, min(1000, int(z)))
        r = max(-1000, min(1000, int(r)))
        with self.write_lock:
            self.master.mav.manual_control_send(
                self.master.target_system,
                x,
                y,
                z,
                r,
                0,
            )

    def update_crop_health(self, crop: str, healthy: float, wilting: float, disease: float, pest: float):
        """Call this from your Edge-AI inference pipeline after each aggregation window."""
        crop = crop.lower()
        if crop not in self.crop_health:
            raise ValueError(f"Unsupported crop: {crop}")
        vals = [healthy, wilting, disease, pest]
        if any(v < 0 or v > 100 for v in vals):
            raise ValueError("Crop-health values must be 0..100")
        with self.state_lock:
            self.crop_health[crop] = {
                "healthy": round(float(healthy), 1),
                "wilting": round(float(wilting), 1),
                "disease": round(float(disease), 1),
                "pest": round(float(pest), 1),
            }
        if disease >= 10 or pest >= 10:
            self.log(f"{crop.upper()} alert: disease={disease:.1f}% pest={pest:.1f}%", "ALERT")


bridge = MavlinkBridge()


class CameraService:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_jpeg: Optional[bytes] = None
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.source = "uninitialized"

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="camera")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _annotate(self, frame: np.ndarray) -> np.ndarray:
        # Edge-AI integration point. Draw your real detections here.
        cv2.putText(
            frame,
            "Edge AI hook ready",
            (16, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return frame

    def _encode(self, frame):
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ok:
            with self.lock:
                self.latest_jpeg = buf.tobytes()

    def _run(self):
        picam = None
        cap = None
        try:
            if Picamera2 is not None and os.getenv("USE_PICAMERA2", "1") == "1":
                picam = Picamera2()
                config = picam.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
                picam.configure(config)
                picam.start()
                self.source = "picamera2"
                bridge.log("Camera started with Picamera2")
            else:
                cap = cv2.VideoCapture(int(os.getenv("OPENCV_CAMERA_INDEX", "0")))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.source = "opencv"
                bridge.log("Camera started with OpenCV VideoCapture")

            while not self.stop_event.is_set():
                if picam is not None:
                    frame = picam.capture_array()
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    ok, frame = cap.read() if cap is not None else (False, None)
                    if not ok:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.putText(frame, "NO CAMERA FRAME", (175, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
                frame = self._annotate(frame)
                self._encode(frame)
                time.sleep(0.06)
        except Exception as exc:
            bridge.log(f"Camera error: {exc}", "WARN")
        finally:
            try:
                if picam is not None:
                    picam.stop()
            except Exception:
                pass
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass

    def frame(self) -> Optional[bytes]:
        with self.lock:
            return self.latest_jpeg


camera = CameraService()


def current_ssid() -> str:
    if os.getenv("SIMULATE_HOME_WIFI", "0") == "1":
        return HOME_SSID or "SIMULATED_HOME"

    commands = [
        ["iwgetid", "-r"],
        ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            text = (result.stdout or "").strip()
            if not text:
                continue
            if cmd[0] == "nmcli":
                for line in text.splitlines():
                    if line.startswith("yes:"):
                        return line.split(":", 1)[1]
            return text.splitlines()[0]
        except Exception:
            continue
    return ""


def sync_status() -> Dict[str, Any]:
    ssid = current_ssid()
    latest_id = store.latest_telemetry_id()
    last_sync_id = int(store.get_meta("last_sync_telemetry_id", "0") or 0)
    pending = latest_id > last_sync_id
    on_home_wifi = bool(HOME_SSID) and ssid == HOME_SSID
    return {
        "ssid": ssid,
        "on_home_wifi": on_home_wifi,
        "pending": pending,
        "show_prompt": on_home_wifi and pending,
        "google_drive_configured": bool(GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_DRIVE_FOLDER_ID),
    }


def package_store_and_forward() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = DATA_DIR / f"puray-flight-sync-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in DATA_DIR.rglob("*"):
            if p == zip_path or not p.is_file() or p.suffix == ".zip":
                continue
            zf.write(p, p.relative_to(DATA_DIR))
    return zip_path


def upload_to_google_drive(zip_path: Path) -> str:
    if not (GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_DRIVE_FOLDER_ID):
        raise RuntimeError(
            "Google Drive sync is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_DRIVE_FOLDER_ID."
        )
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Install optional Google packages: google-api-python-client google-auth"
        ) from exc

    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    body = {"name": zip_path.name, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    media = MediaFileUpload(str(zip_path), mimetype="application/zip", resumable=True)
    result = service.files().create(body=body, media_body=media, fields="id,name").execute()
    return result["id"]


app = FastAPI(title="Puray Drone GCS", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
async def startup_event():
    bridge.start()
    camera.start()
    app.state.telemetry_logger = asyncio.create_task(telemetry_logger_loop())


@app.on_event("shutdown")
async def shutdown_event():
    bridge.stop()
    camera.stop()
    task = getattr(app.state, "telemetry_logger", None)
    if task:
        task.cancel()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mavlink_connected": bridge.connected,
        "camera_source": camera.source,
        "sync": sync_status(),
    }


@app.get("/api/sync/status")
def api_sync_status():
    return sync_status()


@app.post("/api/sync")
def api_sync(x_control_token: str = Header(default="")):
    if x_control_token != CONTROL_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid control token")
    status = sync_status()
    if not status["on_home_wifi"]:
        raise HTTPException(status_code=409, detail="Not connected to configured home Wi-Fi")
    try:
        zip_path = package_store_and_forward()
        file_id = upload_to_google_drive(zip_path)
        store.set_meta("last_sync_telemetry_id", str(store.latest_telemetry_id()))
        bridge.log(f"Google Drive sync complete: file_id={file_id}")
        return {"ok": True, "file_id": file_id, "archive": zip_path.name}
    except Exception as exc:
        bridge.log(f"Google Drive sync failed: {exc}", "WARN")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/video_feed")
def video_feed():
    def generate():
        while True:
            frame = camera.frame()
            if frame is None:
                time.sleep(0.1)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.08)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


async def telemetry_logger_loop():
    while True:
        try:
            snapshot = bridge.snapshot()["telemetry"]
            snapshot["mavlink_connected"] = bridge.connected
            store.add_telemetry(snapshot)
        except Exception as exc:
            bridge.log(f"Telemetry store error: {exc}", "WARN")
        await asyncio.sleep(1.0)


async def ws_sender(ws: WebSocket):
    while True:
        snap = bridge.snapshot()
        snap["sync"] = sync_status()
        await ws.send_json(snap)
        await asyncio.sleep(0.25)


async def ws_receiver(ws: WebSocket, session: Dict[str, Any]):
    while True:
        data = await ws.receive_json()
        msg_type = str(data.get("type", ""))

        if not session["authenticated"]:
            if msg_type != "auth" or data.get("token") != CONTROL_TOKEN:
                await ws.send_json({"type": "error", "message": "Authentication required"})
                continue
            session["authenticated"] = True
            session["last_operator_heartbeat"] = time.monotonic()
            await ws.send_json({"type": "auth_ok"})
            continue

        if msg_type == "operator_heartbeat":
            session["last_operator_heartbeat"] = time.monotonic()

        elif msg_type == "take_control":
            session["control_taken"] = bool(data.get("enabled", False))
            session["last_operator_heartbeat"] = time.monotonic()
            await ws.send_json({"type": "control_lease", "enabled": session["control_taken"]})

        elif msg_type == "manual":
            if not session["control_taken"]:
                await ws.send_json({"type": "error", "message": "Manual control lease is not enabled"})
                continue
            session["last_operator_heartbeat"] = time.monotonic()
            bridge.manual_control(data.get("x", 0), data.get("y", 0), data.get("z", 500), data.get("r", 0))

        elif msg_type == "set_mode":
            bridge.set_mode(str(data.get("mode", "")))

        elif msg_type == "command":
            cmd = str(data.get("command", "")).upper()
            if cmd == "ARM":
                bridge.arm()
            elif cmd == "DISARM":
                bridge.disarm()
            elif cmd == "TAKEOFF":
                bridge.takeoff(float(data.get("altitude_m", TAKEOFF_ALT_M)))
            elif cmd == "RTL":
                bridge.rtl()
            elif cmd == "LAND":
                bridge.land()
            elif cmd == "EMERGENCY_STOP":
                result = bridge.emergency_stop()
                await ws.send_json({"type": "emergency_action", "action": result})
            else:
                raise RuntimeError(f"Unknown command: {cmd}")

        else:
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session = {
        "authenticated": False,
        "control_taken": False,
        "last_operator_heartbeat": time.monotonic(),
    }
    bridge.log("UI WebSocket connected")

    sender = asyncio.create_task(ws_sender(ws))
    receiver = asyncio.create_task(ws_receiver(ws, session))

    try:
        while True:
            done, _ = await asyncio.wait(
                {sender, receiver}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
            )
            if done:
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
                break

            if (
                session["authenticated"]
                and session["control_taken"]
                and bridge.snapshot()["telemetry"].get("armed")
                and time.monotonic() - session["last_operator_heartbeat"] > CONTROL_LINK_TIMEOUT_S
            ):
                session["control_taken"] = False
                try:
                    bridge.rtl()
                    bridge.log("Operator-link timeout while manually controlled: RTL requested", "WARN")
                except Exception as exc:
                    bridge.log(f"Operator-link timeout RTL failed: {exc}", "WARN")

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
        bridge.log(f"UI WebSocket error: {exc}", "WARN")
    finally:
        sender.cancel()
        receiver.cancel()
        if session.get("control_taken") and bridge.snapshot()["telemetry"].get("armed"):
            try:
                bridge.rtl()
                bridge.log("Control UI disconnected while manually controlled: RTL requested", "WARN")
            except Exception as exc:
                bridge.log(f"Disconnect RTL failed: {exc}", "WARN")
        bridge.log("UI WebSocket disconnected")

from __future__ import annotations

import asyncio
import io
import json
import math
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
from uuid import uuid4

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
TELEMETRY_MAX_AGE_S = float(os.getenv("TELEMETRY_MAX_AGE_S", "3.0"))
COMMAND_TIMEOUT_S = float(os.getenv("COMMAND_TIMEOUT_S", "8.0"))

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
        self.log_lock = threading.Lock()
        self.connected = False
        self.last_msg_monotonic = 0.0
        self.logs = deque(maxlen=150)
        self.last_command_ack: Optional[dict] = None
        self.message_times: Dict[str, float] = {}
        self.command_acks = deque(maxlen=100)

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
            for crop in ("okra", "talong", "sili")
        }

    def log(self, message: str, level: str = "INFO"):
        item = {"ts": utc_iso(), "level": level, "message": message}
        with self.log_lock:
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
        with self.state_lock:
            self.message_times.clear()
            self.command_acks.clear()
        self._handle_msg(hb)
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
        # Ignore other vehicles/components, including another GCS heartbeat.
        if self.master is None or (
            msg.get_srcSystem() != self.master.target_system
            or msg.get_srcComponent() != self.master.target_component
        ):
            return

        with self.state_lock:
            now = time.monotonic()
            self.last_msg_monotonic = now
            self.message_times[t] = now
            self.connected = True
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
                self.command_acks.append((now, dict(self.last_command_ack)))
                self.log(f"COMMAND_ACK command={msg.command} result={msg.result}")

    def snapshot(self) -> Dict[str, Any]:
        with self.state_lock:
            state = dict(self.state)
            analytics = json.loads(json.dumps(self.crop_health))
        with self.log_lock:
            logs = list(self.logs)[-30:]
        return {
            "type": "telemetry",
            "ts": utc_iso(),
            "mavlink_connected": self.connected and self.is_fresh("HEARTBEAT"),
            "telemetry": state,
            "crop_health": analytics,
            "logs": logs,
            "last_command_ack": self.last_command_ack,
        }

    def _require_link(self):
        if self.master is None or not self.connected:
            raise RuntimeError("Flight controller is not connected over MAVLink")

    def is_fresh(self, message_type: str) -> bool:
        with self.state_lock:
            received = self.message_times.get(message_type)
        return received is not None and time.monotonic() - received <= TELEMETRY_MAX_AGE_S

    def require_fresh(self, *message_types: str):
        self._require_link()
        for message_type in ("HEARTBEAT", *message_types):
            if not self.is_fresh(message_type):
                raise RuntimeError(f"Missing/stale {message_type} telemetry; command blocked")

    def require_preflight(self):
        self.require_fresh("SYS_STATUS", "GPS_RAW_INT")
        snap = self.snapshot()["telemetry"]
        if snap["battery_pct"] is None or snap["battery_pct"] < 20:
            raise RuntimeError("Battery unknown or below 20%; command blocked")
        if snap["gps_fix"] is None or snap["gps_fix"] < 3:
            raise RuntimeError("GPS needs a current 3D fix; command blocked")

    def _send_command_long(self, command: int, params=None):
        self.require_fresh()
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
        self.require_preflight()
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
        self.require_fresh()
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
        self.require_preflight()
        if not math.isfinite(altitude_m) or altitude_m <= 0:
            raise ValueError("Takeoff altitude must be a finite positive number")
        snap = self.snapshot()["telemetry"]
        if not snap.get("armed"):
            raise RuntimeError("TAKEOFF blocked: vehicle is not armed")
        if snap.get("mode") != "GUIDED":
            raise RuntimeError("TAKEOFF blocked: GUIDED mode is not confirmed")
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
        self.require_fresh()
        landed = (self.snapshot()["telemetry"].get("landed_state")
                  if self.is_fresh("EXTENDED_SYS_STATE") else None)
        on_ground = landed == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
        if on_ground:
            self.disarm()
            self.log("EMERGENCY STOP: on-ground disarm requested", "WARN")
            return "disarm"
        self.land()
        self.log("EMERGENCY STOP: airborne motor-kill blocked; LAND requested", "WARN")
        return "land"

    def manual_control(self, x: int, y: int, z: int, r: int):
        self.require_fresh()
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


class ControlCoordinator:
    """Process-wide ownership; accessed only by the ASGI event loop.

    Run one Uvicorn worker: the MAVLink connection and lease belong to this process.
    """

    def __init__(self):
        self.owner: Optional[str] = None
        self.last_heartbeat = 0.0
        self.releasing = False
        self.command_task: Optional[asyncio.Task] = None
        self.command_session: Optional[str] = None
        self.command_switching = False

    def status(self, session_id: str):
        return {"enabled": self.owner == session_id and not self.releasing,
                "occupied": self.owner is not None or self.releasing}

    def command_busy(self):
        return self.command_switching or (
            self.command_task is not None and not self.command_task.done())

    def claim(self, session_id: str):
        if self.releasing or self.owner not in (None, session_id):
            raise RuntimeError("Another session owns manual control")
        if self.command_busy():
            raise RuntimeError("Wait for the pending flight command before taking control")
        bridge.require_fresh()
        self.owner = session_id
        self.last_heartbeat = time.monotonic()

    async def release(self, session_id: str, reason: str, failsafe: bool):
        if self.owner != session_id or self.releasing:
            return
        self.releasing = True
        try:
            if self.command_session == session_id and self.command_busy():
                self.command_task.cancel()
                await asyncio.gather(self.command_task, return_exceptions=True)
            try:
                if failsafe and bridge.snapshot()["telemetry"].get("armed"):
                    bridge.rtl()
                    bridge.log(f"{reason}: RTL requested", "WARN")
                elif not failsafe:
                    bridge.manual_control(0, 0, 500, 0)
            except Exception as exc:
                bridge.log(f"{reason}: control release action failed: {exc}", "WARN")
        finally:
            self.owner = None
            self.releasing = False


control = ControlCoordinator()


async def send_session(ws: WebSocket, session: Dict[str, Any], payload: dict):
    # Telemetry, command results and authentication share the same socket.
    async with session["send_lock"]:
        async with asyncio.timeout(2.0):
            await ws.send_json(payload)


async def wait_command(command: int, sent_at: float, mode: Optional[str] = None):
    """An ACK accepts a request; mode changes require a new confirming heartbeat."""
    deadline = time.monotonic() + COMMAND_TIMEOUT_S
    while time.monotonic() < deadline:
        with bridge.state_lock:
            acks = list(bridge.command_acks)
            mode_confirmed = (
                mode is not None and bridge.state["mode"] == mode
                and bridge.message_times.get("HEARTBEAT", 0) >= sent_at
            )
        for received, ack in acks:
            if received < sent_at or ack["command"] != command:
                continue
            result = ack["result"]
            if result not in (mavutil.mavlink.MAV_RESULT_ACCEPTED,
                              mavutil.mavlink.MAV_RESULT_IN_PROGRESS):
                raise RuntimeError(f"Flight controller rejected command (MAV_RESULT={result})")
            if result == mavutil.mavlink.MAV_RESULT_ACCEPTED and mode is None:
                return "Flight controller accepted the request (completion not implied)"
        if mode_confirmed and bridge.is_fresh("HEARTBEAT"):
            return f"Flight controller confirmed {mode} mode"
        await asyncio.sleep(0.05)
    raise TimeoutError("No final acknowledgment/mode confirmation; outcome unknown, not retried")


async def confirmed_mode(mode: str):
    sent_at = time.monotonic()
    bridge.set_mode(mode)
    return await wait_command(mavutil.mavlink.MAV_CMD_DO_SET_MODE, sent_at, mode)


async def execute_flight_command(data: dict):
    if data["type"] == "set_mode":
        return await confirmed_mode(str(data.get("mode", "")).upper())
    cmd = str(data.get("command", "")).upper()
    if cmd in ("RTL", "LAND"):
        return await confirmed_mode(cmd)
    if cmd == "EMERGENCY_STOP":
        sent_at = time.monotonic()
        action = bridge.emergency_stop()
        command = (mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM if action == "disarm"
                   else mavutil.mavlink.MAV_CMD_DO_SET_MODE)
        return await wait_command(command, sent_at, None if action == "disarm" else "LAND")
    if cmd == "TAKEOFF":
        altitude = float(data.get("altitude_m", TAKEOFF_ALT_M))
        if not math.isfinite(altitude) or altitude <= 0:
            raise ValueError("Takeoff altitude must be a finite positive number")
        bridge.require_preflight()
        if not bridge.snapshot()["telemetry"].get("armed"):
            raise RuntimeError("TAKEOFF blocked: vehicle is not armed")
        await confirmed_mode("GUIDED")
        sent_at = time.monotonic()
        bridge.takeoff(altitude)
        return await wait_command(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, sent_at)
    if cmd in ("ARM", "DISARM"):
        sent_at = time.monotonic()
        (bridge.arm if cmd == "ARM" else bridge.disarm)()
        return await wait_command(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, sent_at)
    raise ValueError(f"Unknown command: {cmd}")


async def run_flight_command(ws: WebSocket, session: dict, data: dict):
    result = {"type": "command_status", "request_id": data["request_id"]}
    try:
        await send_session(ws, session, {**result, "status": "pending", "message": "Waiting for flight controller"})
        message = await execute_flight_command(data)
        status = "accepted"
    except asyncio.CancelledError:
        # Cancellation must stop any later stage (especially GUIDED -> TAKEOFF).
        # If the socket is still open, report the uncertain outcome.
        try:
            await send_session(ws, session, {**result, "status": "timeout", "message": "Control session ended; outcome unknown"})
        except Exception:
            pass
        raise
    except TimeoutError as exc:
        status, message = "timeout", str(exc)
    except Exception as exc:
        status, message = "rejected", str(exc)
    await send_session(ws, session, {**result, "status": status, "message": message})


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
    app.state.sync_poller = asyncio.create_task(sync_status_loop())


@app.on_event("shutdown")
async def shutdown_event():
    bridge.stop()
    camera.stop()
    for name in ("telemetry_logger", "sync_poller"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mavlink_connected": bridge.snapshot()["mavlink_connected"],
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
            current = bridge.snapshot()
            snapshot = current["telemetry"]
            snapshot["mavlink_connected"] = current["mavlink_connected"]
            await asyncio.to_thread(store.add_telemetry, snapshot)
        except Exception as exc:
            bridge.log(f"Telemetry store error: {exc}", "WARN")
        await asyncio.sleep(1.0)


async def sync_status_loop():
    while True:
        try:
            app.state.sync_status = await asyncio.to_thread(sync_status)
        except Exception as exc:
            bridge.log(f"Sync status error: {exc}", "WARN")
        await asyncio.sleep(5)


async def ws_sender(ws: WebSocket, session: Dict[str, Any]):
    while True:
        snap = bridge.snapshot()
        snap["sync"] = getattr(app.state, "sync_status", {})
        snap["control"] = control.status(session["id"])
        await send_session(ws, session, snap)
        await asyncio.sleep(0.25)


async def ws_receiver(ws: WebSocket, session: Dict[str, Any]):
    while True:
        data = None
        try:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            msg_type = str(data.get("type", ""))
            if not session["authenticated"]:
                if msg_type != "auth" or data.get("token") != CONTROL_TOKEN:
                    await send_session(ws, session, {"type": "auth_failed", "message": "Invalid control token"})
                    continue
                session["authenticated"] = True
                await send_session(ws, session, {"type": "auth_ok", "command_timeout_s": 2 * COMMAND_TIMEOUT_S + 5})
                continue

            if msg_type == "operator_heartbeat":
                if control.owner == session["id"]:
                    control.last_heartbeat = time.monotonic()
            elif msg_type == "take_control":
                if not isinstance(data.get("enabled"), bool):
                    raise ValueError("enabled must be a boolean")
                if data["enabled"]:
                    control.claim(session["id"])
                else:
                    await control.release(session["id"], "Manual control disabled", failsafe=False)
                await send_session(ws, session, {"type": "control_lease", **control.status(session["id"])})
            elif msg_type == "manual":
                if not control.status(session["id"])["enabled"]:
                    raise RuntimeError("This session does not own manual control")
                bridge.manual_control(data.get("x", 0), data.get("y", 0),
                                      data.get("z", 500), data.get("r", 0))
                control.last_heartbeat = time.monotonic()
            elif msg_type in ("command", "set_mode"):
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                    raise ValueError("A request_id of 1..100 characters is required")
                if request_id in session["request_ids"]:
                    raise ValueError("Duplicate command request; not resent")
                session["request_ids"].append(request_id)
                if control.releasing or control.owner not in (None, session["id"]):
                    raise RuntimeError("Another session owns manual control")
                if control.command_switching:
                    raise RuntimeError("An emergency command is being prepared")
                if control.command_busy():
                    if msg_type != "command" or data.get("command") != "EMERGENCY_STOP":
                        raise RuntimeError("A flight command is already pending")
                    # Emergency LAND/disarm must not wait behind an ACK timeout.
                    control.command_switching = True
                    try:
                        control.command_task.cancel()
                        await asyncio.gather(control.command_task, return_exceptions=True)
                    finally:
                        control.command_switching = False
                control.command_session = session["id"]
                task = asyncio.create_task(run_flight_command(ws, session, data))
                control.command_task = task
                session["command_task"] = task
                # Retrieve transport exceptions even if the connection closes first.
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            else:
                raise ValueError(f"Unknown message type: {msg_type}")
        except WebSocketDisconnect:
            raise
        except Exception as exc:
            # A rejected or malformed command must not tear down the connection.
            payload = {"type": "error", "message": str(exc)}
            if isinstance(data, dict):
                if data.get("type") in ("command", "set_mode"):
                    payload.update(type="command_status", request_id=data.get("request_id"), status="rejected")
                if data.get("type") in ("take_control", "manual"):
                    if data.get("type") == "manual":
                        await control.release(session["id"], "Manual input rejected", failsafe=True)
                    await send_session(ws, session, {"type": "control_lease", **control.status(session["id"])})
            await send_session(ws, session, payload)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session = {"id": uuid4().hex, "authenticated": False,
               "send_lock": asyncio.Lock(), "command_task": None,
               "request_ids": deque(maxlen=100)}
    bridge.log("UI WebSocket connected")
    sender = asyncio.create_task(ws_sender(ws, session))
    receiver = asyncio.create_task(ws_receiver(ws, session))
    try:
        while True:
            done, _ = await asyncio.wait(
                {sender, receiver}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
            )
            if done:
                for task in done:
                    task.result()
                break
            if (control.owner == session["id"] and
                    (time.monotonic() - control.last_heartbeat > CONTROL_LINK_TIMEOUT_S
                     or not bridge.is_fresh("HEARTBEAT"))):
                await control.release(session["id"], "Operator/telemetry link timeout", failsafe=True)
                await send_session(ws, session, {"type": "control_lease", **control.status(session["id"])})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        bridge.log(f"UI WebSocket error: {exc}", "WARN")
    finally:
        sender.cancel()
        receiver.cancel()
        command_task = session["command_task"]
        if command_task and not command_task.done():
            command_task.cancel()
        await asyncio.gather(sender, receiver, *([command_task] if command_task else []), return_exceptions=True)
        await control.release(session["id"], "Control UI disconnected", failsafe=True)
        try:
            await ws.close()
        except Exception:
            pass
        bridge.log("UI WebSocket disconnected")

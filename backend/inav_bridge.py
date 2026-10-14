"""INAV MSP telemetry bridge (read-only, version 1).

Drop-in replacement for the telemetry side of MavlinkBridge.
Flight commands are intentionally NOT implemented yet and raise RuntimeError.
"""
import json
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import serial

MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_RAW_GPS = 106
MSP_ATTITUDE = 108
MSP_ANALOG = 110

# INAV raw fix (0 none, 1 2D, 2 3D) -> MAVLink-style fix_type used by the old code.
# ASSUMPTION: to be verified outdoors with a real fix.
FIX_MAP = {0: 0, 1: 2, 2: 3}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class InavBridge:
    PROTOCOL = "inav_msp"

    def __init__(self, port: str = "/dev/ttyACM0", baud: int = 115200,
                 max_age_s: float = 2.0,
                 log_sink: Optional[Callable[[str, str], None]] = None):
        self.port = port
        self.baud = baud
        self.max_age_s = max_age_s
        self.log_sink = log_sink
        self.ser: Optional[serial.Serial] = None
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
        self._next_gps = 0.0
        self._next_analog = 0.0

        # None / "UNKNOWN" means unavailable (never invented).
        self.state: Dict[str, Any] = {
            "armed": None,
            "mode": "UNKNOWN",
            "battery_pct": None,
            "voltage_v": None,
            "current_a": None,
            "gps_sats": None,
            "gps_fix": None,
            "gps_fix_raw": None,
            "gps_hdop": None,
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

        self.crop_health: Dict[str, Dict[str, Optional[float]]] = {
            crop: {"healthy": None, "wilting": None, "disease": None, "pest": None}
            for crop in ("okra", "talong", "sili")
        }

    # ---------- logging / lifecycle ----------
    def log(self, message: str, level: str = "INFO"):
        item = {"ts": _utc_iso(), "level": level, "message": message}
        with self.log_lock:
            self.logs.append(item)
        if self.log_sink:
            try:
                self.log_sink(level, message)
            except Exception:
                pass
        print(f"[{level}] {message}")

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="inav-msp-reader")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    # ---------- serial / MSP ----------
    def _close(self):
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _msp_request(self, cmd: int, timeout: float = 0.5) -> Optional[bytes]:
        ser = self.ser
        if ser is None:
            raise serial.SerialException("serial port is not open")
        with self.write_lock:
            ser.reset_input_buffer()
            ser.write(bytes([0x24, 0x4D, 0x3C, 0x00, cmd, cmd]))  # $M< size=0 cmd checksum
            deadline = time.monotonic() + timeout
            buf = bytearray()
            while time.monotonic() < deadline:
                buf += ser.read(ser.in_waiting or 1)
                while True:
                    i = buf.find(b"$M")
                    if i < 0:
                        if len(buf) > 64:
                            del buf[:-1]
                        break
                    if len(buf) < i + 5:
                        break
                    direction = buf[i + 2]
                    size = buf[i + 3]
                    c = buf[i + 4]
                    end = i + 6 + size
                    if len(buf) < end:
                        break
                    payload = bytes(buf[i + 5:i + 5 + size])
                    chk = buf[i + 5 + size]
                    calc = size ^ c
                    for b in payload:
                        calc ^= b
                    del buf[:end]
                    if calc != chk or c != cmd:
                        continue
                    if direction == 0x21:  # '!' error reply
                        return None
                    if direction == 0x3E:  # '>' normal reply
                        return payload
            return None

    def _open(self):
        self.log(f"Opening INAV MSP on {self.port} @ {self.baud}")
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        time.sleep(0.3)
        variant = self._msp_request(MSP_FC_VARIANT, timeout=1.0)
        if variant is None:
            raise TimeoutError("No MSP reply from flight controller")
        if variant != b"INAV":
            raise RuntimeError(f"Expected INAV firmware, got {variant!r}")
        ver = self._msp_request(MSP_FC_VERSION, timeout=1.0)
        if ver and len(ver) >= 3:
            self.log(f"INAV {ver[0]}.{ver[1]}.{ver[2]} detected")
        with self.state_lock:
            self.message_times.clear()
            self.command_acks.clear()
        self.last_msg_monotonic = time.monotonic()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.ser is None:
                    self._open()
                self._poll_once()
            except Exception as exc:
                self.connected = False
                self.log(f"INAV link error, retrying: {exc}", "WARN")
                self._close()
                self.stop_event.wait(2.0)
        self._close()

    def _poll(self, cmd: int, handler):
        payload = self._msp_request(cmd)
        if payload is not None:
            handler(payload)

    def _poll_once(self):
        now = time.monotonic()
        self._poll(MSP_ATTITUDE, self._on_attitude)
        if now >= self._next_gps:
            self._poll(MSP_RAW_GPS, self._on_gps)
            self._next_gps = now + 0.5
        if now >= self._next_analog:
            self._poll(MSP_ANALOG, self._on_analog)
            self._next_analog = now + 1.0
        if time.monotonic() - self.last_msg_monotonic > 3.0:
            raise TimeoutError("No valid MSP replies for 3 s")
        self.stop_event.wait(0.05)

    # ---------- decoders (call only the matching MSP reply) ----------
    def _stamp_locked(self, name: str):
        now = time.monotonic()
        self.message_times[name] = now
        self.message_times["HEARTBEAT"] = now  # any valid reply counts as link-alive
        self.last_msg_monotonic = now
        self.connected = True

    def _on_attitude(self, p: bytes):
        if len(p) < 6:
            return
        roll, pitch, yaw = struct.unpack("<hhh", p[:6])
        with self.state_lock:
            self.state["roll_deg"] = roll / 10.0
            self.state["pitch_deg"] = pitch / 10.0
            self.state["yaw_deg"] = float(yaw)  # gyro-estimated: no compass fitted
            self._stamp_locked("ATTITUDE")

    def _on_gps(self, p: bytes):
        if len(p) < 16:
            return
        fix, sats, lat, lon, alt, spd, _course = struct.unpack("<BBiihHH", p[:16])
        hdop_raw = struct.unpack("<H", p[16:18])[0] if len(p) >= 18 else None
        has_fix = fix >= 1
        with self.state_lock:
            self.state["gps_fix_raw"] = fix
            self.state["gps_fix"] = FIX_MAP.get(fix, 0)
            self.state["gps_sats"] = sats
            self.state["gps_hdop"] = None if hdop_raw in (None, 9999) else hdop_raw / 100.0
            self.state["lat"] = lat / 1e7 if has_fix else None
            self.state["lon"] = lon / 1e7 if has_fix else None
            self.state["alt_m"] = float(alt) if has_fix else None
            self.state["groundspeed_mps"] = round(spd / 100.0, 2) if has_fix else None
            self._stamp_locked("GPS_RAW_INT")

    def _on_analog(self, p: bytes):
        if len(p) < 7:
            return
        vbat, _mah, _rssi, amps = struct.unpack("<BHHh", p[:7])
        with self.state_lock:
            self.state["voltage_v"] = round(vbat / 10.0, 1)  # 8-bit field, max 25.5 V
            self.state["current_a"] = round(amps / 100.0, 2)
            self._stamp_locked("SYS_STATUS")

    # ---------- interface expected by main_backend.py ----------
    def snapshot(self) -> Dict[str, Any]:
        with self.state_lock:
            state = dict(self.state)
            analytics = json.loads(json.dumps(self.crop_health))
        with self.log_lock:
            logs = list(self.logs)[-30:]
        return {
            "type": "telemetry",
            "ts": _utc_iso(),
            "protocol": self.PROTOCOL,
            "mavlink_connected": self.connected and self.is_fresh("HEARTBEAT"),
            "telemetry": state,
            "crop_health": analytics,
            "logs": logs,
            "last_command_ack": self.last_command_ack,
        }

    def is_fresh(self, message_type: str) -> bool:
        with self.state_lock:
            received = self.message_times.get(message_type)
        return received is not None and time.monotonic() - received <= self.max_age_s

    def _require_link(self):
        if self.ser is None or not self.connected:
            raise RuntimeError("Flight controller is not connected over MSP")

    def require_fresh(self, *message_types: str):
        self._require_link()
        for message_type in ("HEARTBEAT", *message_types):
            if not self.is_fresh(message_type):
                raise RuntimeError(f"Missing/stale {message_type} telemetry; command blocked")

    def _unsupported(self, what: str):
        msg = f"{what} is not implemented for INAV yet; nothing was sent"
        self.log(msg, "WARN")
        raise RuntimeError(msg)

    def require_preflight(self):
        self._unsupported("Preflight check")

    def arm(self):
        self._unsupported("ARM")

    def disarm(self):
        self._unsupported("DISARM")

    def set_mode(self, mode: str):
        self._unsupported(f"SET_MODE {mode}")

    def takeoff(self, *args, **kwargs):
        self._unsupported("TAKEOFF")

    def rtl(self):
        self._unsupported("RTL")

    def land(self):
        self._unsupported("LAND")

    def emergency_stop(self):
        self._unsupported("EMERGENCY STOP")

    def manual_control(self, *args, **kwargs):
        self._unsupported("Manual control")


if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
    bridge = InavBridge(port=port)
    bridge.start()
    try:
        for _ in range(12):
            time.sleep(1)
            snap = bridge.snapshot()
            t = snap["telemetry"]
            print(f"connected={snap['mavlink_connected']} roll={t['roll_deg']} "
                  f"pitch={t['pitch_deg']} yaw={t['yaw_deg']} fix={t['gps_fix']} "
                  f"sats={t['gps_sats']} vbat={t['voltage_v']}")
    finally:
        bridge.stop()
        time.sleep(0.5)

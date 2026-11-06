"""Same as before, but Phase 1 now prints every poll so we can see
WHY it never reached a clean baseline. Props OFF. LiPo disconnected. FC USB-only."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200
MSP2_INAV_STATUS = 0x2000

FLAG_NAMES = {
    2: "ARMED", 3: "WAS_EVER_ARMED", 4: "SIM_HITL", 5: "SIM_SITL",
    6: "GEOZONE", 7: "FAILSAFE_SYSTEM", 8: "NOT_LEVEL", 9: "SENSORS_CALIBRATING",
    10: "SYSTEM_OVERLOADED", 11: "NAVIGATION_UNSAFE", 12: "COMPASS_NOT_CALIBRATED",
    13: "ACCELEROMETER_NOT_CALIBRATED", 14: "ARM_SWITCH", 15: "HARDWARE_FAILURE",
    16: "BOXFAILSAFE", 18: "RC_LINK", 19: "THROTTLE", 20: "CLI", 21: "CMS_MENU",
    22: "OSD_MENU", 23: "ROLLPITCH_NOT_CENTERED", 24: "SERVO_AUTOTRIM", 25: "OOM",
    26: "INVALID_SETTING", 27: "PWM_OUTPUT_ERROR", 28: "NO_PREARM",
    29: "DSHOT_BEEPER", 30: "LANDING_DETECTED",
}

def decode_flags(armf):
    return [name for bit, name in FLAG_NAMES.items() if armf & (1 << bit)]

bridge = InavBridge()
bridge._open()
print(f"Connected: {bridge.connected}")

def send_frame(ch5):
    channels = [1500, 1500, 1500, 1000, ch5, 1500, 1500, 1500]
    payload = struct.pack("<8H", *channels)
    return bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)

def read_full_status():
    raw = bridge._msp2_request(MSP2_INAV_STATUS, timeout=0.5)
    if raw is None or len(raw) < 13:
        return None
    cyc, i2c, sens, load, prof, armf = struct.unpack_from("<HHHHBI", raw, 0)
    return armf

print("Phase 1: holding DISARM, watching armingFlags for 20s...")
t0 = time.monotonic()
clean_streak = 0
ready = False
while time.monotonic() - t0 < 20:
    t = time.monotonic() - t0
    send_frame(1000)
    armf = read_full_status()
    if armf is not None:
        flags = decode_flags(armf)
        print(f"t={t:4.1f}s  armingFlags=0x{armf:08x}  active={flags}")
        if not flags:
            clean_streak += 1
        else:
            clean_streak = 0
        if clean_streak >= 3:
            ready = True
            break
    time.sleep(0.3)

if not ready:
    print(">>> Never reached clean baseline within 20s.")
else:
    print(">>> Clean baseline confirmed.")

send_frame(1000)
bridge._close()
print("Done.")

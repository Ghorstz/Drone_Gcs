"""
Test: stream MSP_SET_RAW_RC at high rate (~20-30Hz) continuously, interleaving
periodic MSP2_INAV_STATUS reads WITHOUT letting them slow the RC cadence
below INAV's ~200ms (DELAY_5_HZ) rxSignalTimeout window for RX_TYPE_MSP
(source-confirmed in rx/msp.c: rxMspInit() sets rxSignalTimeout = DELAY_5_HZ).

Hypothesis (source-derived, NOT yet confirmed): our prior diagnostic loop's
RC frame interval (~350-800ms, due to blocking status reads + sleep(0.3))
exceeded that window, so rxSignalReceived flickered back to false before
each failsafeOnValidDataReceived() check could run, resetting
validRxDataFailedAt every cycle and preventing rxLinkState from ever
latching UP -- keeping ARMING_DISABLED_RC_LINK permanently set.

This script tests whether fast, continuous RC streaming clears it.

Props OFF. LiPo disconnected. FC USB-only.
"""
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

def send_frame(ch5=1000):
    channels = [1500, 1500, 1500, 1000, ch5, 1500, 1500, 1500]
    payload = struct.pack("<8H", *channels)
    return bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)

def read_full_status():
    raw = bridge._msp2_request(MSP2_INAV_STATUS, timeout=0.3)
    if raw is None or len(raw) < 13:
        return None
    cyc, i2c, sens, load, prof, armf = struct.unpack_from("<HHHHBI", raw, 0)
    return armf

print("Phase 1: fast RC streaming at DISARM, watching for RC_LINK to clear...")
t0 = time.monotonic()
last_rc_send = t0
status_interval = 0.5   # how often we sample full status (doesn't gate RC cadence)
last_status_check = t0
clean_streak = 0
ready = False
max_rc_gap = 0.0

while time.monotonic() - t0 < 20:
    now = time.monotonic()
    gap = now - last_rc_send
    if gap > max_rc_gap:
        max_rc_gap = gap

    send_frame(1000)
    last_rc_send = time.monotonic()

    if now - last_status_check >= status_interval:
        armf = read_full_status()
        last_status_check = time.monotonic()
        if armf is not None:
            flags = decode_flags(armf)
            t = now - t0
            print(f"t={t:4.1f}s  armingFlags=0x{armf:08x}  active={flags}  max_rc_gap_since_last_print={max_rc_gap*1000:.0f}ms")
            max_rc_gap = 0.0
            if not flags:
                clean_streak += 1
            else:
                clean_streak = 0
            if clean_streak >= 3:
                ready = True
                break
    else:
        time.sleep(0.03)  # target ~20-30Hz RC send rate

if not ready:
    print(">>> Never reached clean baseline within 20s.")
else:
    print(">>> Clean baseline confirmed -- RC_LINK (and other flags) cleared under fast streaming.")

send_frame(1000)
bridge._close()
print("Done.")

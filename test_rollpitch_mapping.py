"""
Verify InavBridge.manual_control()'s roll/pitch arguments correctly map to
the FC's logical Roll/Pitch channels (independent of the already-fixed
throttle/yaw wire-mapping bug, and independent of the separate, already
code-identified dispatch-level swap in main_backend.py's websocket handler).

Reads back MSP_RC (cmd 105), which INAV returns as rcData[] -- already in
LOGICAL order (Roll, Pitch, Yaw, Throttle, AUX1...) after rcmap is applied
on the FC side. So channel[0]=Roll, channel[1]=Pitch directly, no further
translation needed.

SAFETY: LiPo remains DISCONNECTED. Props OFF. FC powered via USB only.
Throttle held at 1000 (minimum) throughout; ARM channel untouched.
"""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_RC = 105

def read_rc_channels(bridge):
    raw = bridge._msp_request(MSP_RC, timeout=0.3)
    if raw is None or len(raw) < 4:
        return None
    n = len(raw) // 2
    return list(struct.unpack(f"<{n}H", raw[:n * 2]))

def fmt(chans):
    if chans is None:
        return "NO DATA"
    labels = ["Roll", "Pitch", "Yaw", "Throttle"]
    parts = [f"{labels[i]}={chans[i]}" if i < len(labels) else f"CH{i+1}={chans[i]}"
             for i in range(min(len(chans), 8))]
    return "  ".join(parts)

bridge = InavBridge()
bridge.start()
time.sleep(1.0)
bridge.start_rc_override()

print("Baseline: all neutral, throttle=1000 (min)...")
bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
time.sleep(1.0)
baseline = read_rc_channels(bridge)
print(f"  {fmt(baseline)}")

print("\nCase A: roll=1800, pitch=1500 (expect Roll≈1800, Pitch≈1500)...")
bridge.manual_control(roll=1800, pitch=1500, throttle=1000, yaw=1500)
time.sleep(1.0)
ch_a = read_rc_channels(bridge)
print(f"  {fmt(ch_a)}")
if ch_a:
    roll_hit = abs(ch_a[0] - 1800) < 50
    pitch_hit = abs(ch_a[1] - 1800) < 50
    if roll_hit and not pitch_hit:
        print("  >>> CORRECT: roll argument reached logical Roll channel.")
    elif pitch_hit and not roll_hit:
        print("  >>> BUG: roll argument reached logical Pitch channel instead!")
    else:
        print("  >>> AMBIGUOUS/UNEXPECTED.")

print("\nReturning to neutral before next case...")
bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
time.sleep(0.5)

print("\nCase B: pitch=1800, roll=1500 (expect Pitch≈1800, Roll≈1500)...")
bridge.manual_control(roll=1500, pitch=1800, throttle=1000, yaw=1500)
time.sleep(1.0)
ch_b = read_rc_channels(bridge)
print(f"  {fmt(ch_b)}")
if ch_b:
    roll_hit = abs(ch_b[0] - 1800) < 50
    pitch_hit = abs(ch_b[1] - 1800) < 50
    if pitch_hit and not roll_hit:
        print("  >>> CORRECT: pitch argument reached logical Pitch channel.")
    elif roll_hit and not pitch_hit:
        print("  >>> BUG: pitch argument reached logical Roll channel instead!")
    else:
        print("  >>> AMBIGUOUS/UNEXPECTED.")

print("\nReturning to neutral, stopping...")
bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
time.sleep(0.3)
bridge.stop_rc_override()
bridge.stop()
time.sleep(0.5)
print("Done.")

"""
Verify the proposed normalized(x,y,z,r) -> raw PWM(roll,pitch,throttle,yaw)
conversion for InavBridge.manual_control(), BEFORE wiring it into
main_backend.py.

Confirmed facts this is based on:
  - frontend app.js: x=pitch-stick(-1000..1000), y=roll-stick(-1000..1000),
    z=throttle(0..1000, resting=500), r=yaw-stick(-1000..1000)
  - InavBridge.manual_control(roll,pitch,throttle,yaw) expects raw PWM
    ~1000-2000, center 1500 (empirically proven this session)

This function is written to be copy-pasted as-is into main_backend.py
once verified here.

SAFETY: LiPo DISCONNECTED. Props OFF. FC via USB only.
"""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_RC = 105


def normalized_to_inav_pwm(x: int, y: int, z: int, r: int):
    """x=pitch-stick, y=roll-stick, z=throttle, r=yaw-stick (frontend convention)
    -> (roll, pitch, throttle, yaw) raw PWM for InavBridge.manual_control()."""
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))
    roll = clamp(1500 + int(y) * 0.5, 1000, 2000)
    pitch = clamp(1500 + int(x) * 0.5, 1000, 2000)
    throttle = clamp(1000 + int(z) * 1.0, 1000, 2000)
    yaw = clamp(1500 + int(r) * 0.5, 1000, 2000)
    return int(roll), int(pitch), int(throttle), int(yaw)


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
    return "  ".join(f"{labels[i]}={chans[i]}" if i < len(labels) else f"CH{i+1}={chans[i]}"
                      for i in range(min(len(chans), 4)))


CASES = [
    # (label, x, y, z, r, expected_description)
    ("Idle/resting stick",        0,    0,    500, 0,  "roll=1500 pitch=1500 throttle=1500 yaw=1500"),
    ("Throttle stick full down",  0,    0,    0,   0,  "throttle=1000 (min)"),
    ("Throttle stick full up",    0,    0,    1000,0,  "throttle=2000 (max)"),
    ("Right stick full RIGHT (roll)",   0, 1000, 0,   0,  "roll=2000, pitch=1500"),
    ("Right stick full UP (pitch)",   1000, 0,    0,   0,  "pitch=2000, roll=1500"),
    ("Left stick full RIGHT (yaw)",     0,    0,    0,   1000, "yaw=2000"),
]

bridge = InavBridge()
bridge.start()
time.sleep(1.0)
bridge.start_rc_override()

print(">>> SAFETY CHECK: LiPo disconnected, props off, FC via USB only.")
input(">>> Press ENTER to begin...")

results = []
for label, x, y, z, r, expected in CASES:
    roll, pitch, throttle, yaw = normalized_to_inav_pwm(x, y, z, r)
    print(f"\n{label}: input(x={x},y={y},z={z},r={r}) -> computed(roll={roll},pitch={pitch},throttle={throttle},yaw={yaw})")
    print(f"  Expected: {expected}")
    bridge.manual_control(roll=roll, pitch=pitch, throttle=throttle, yaw=yaw)
    time.sleep(1.0)
    chans = read_rc_channels(bridge)
    print(f"  FC reports: {fmt(chans)}")
    ok = chans is not None and all(abs(chans[i] - v) < 30 for i, v in enumerate([roll, pitch, yaw, throttle]))
    print(f"  {'PASS' if ok else 'FAIL/REVIEW'}")
    results.append((label, ok))
    # return to safe idle between cases
    bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
    time.sleep(0.3)

print("\n" + "=" * 50)
print("SUMMARY:")
for label, ok in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
print("=" * 50)

bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
time.sleep(0.3)
bridge.stop_rc_override()
bridge.stop()
time.sleep(0.5)
print("Done.")

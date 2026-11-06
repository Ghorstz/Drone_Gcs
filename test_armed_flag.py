"""Determine which MSP_STATUS flag bit actually represents ARMED by comparing
before/after a real arm. Props OFF. LiPo disconnected is fine (USB power)."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_STATUS = 101

def read_flags(bridge, label):
    raw = bridge._msp_request(MSP_STATUS, timeout=1.0)
    if raw is None or len(raw) < 11:
        print(f"[{label}] No/short response: {raw}")
        return None
    cycle_time, i2c_errors, sensors, flags, profile = struct.unpack("<HHHIB", raw[:11])
    print(f"[{label}] flightModeFlags = {flags:032b}  (decimal {flags})")
    return flags

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

print("\n--- BEFORE arm ---")
before = read_flags(bridge, "before")

bridge.start_rc_override()
time.sleep(0.5)

print("\nSending ARM (throttle stays at minimum, safe default)...")
bridge.arm()
time.sleep(2)  # give FC time to actually process the arm

print("\n--- AFTER arm() ---")
after = read_flags(bridge, "after")

if before is not None and after is not None:
    changed = before ^ after
    print(f"\nBits that changed: {changed:032b}")
    for i in range(32):
        if changed & (1 << i):
            print(f"  Bit {i} flipped: {bool(before & (1<<i))} -> {bool(after & (1<<i))}")

print("\nSending DISARM...")
bridge.disarm()
time.sleep(2)

print("\n--- AFTER disarm() ---")
read_flags(bridge, "after-disarm")

bridge.stop_rc_override()
bridge.stop()
print("\nDone.")

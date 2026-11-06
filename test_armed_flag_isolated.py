"""Properly isolated: start RC stream first, wait for it to stabilize, THEN arm,
to avoid conflating "stream started" with "armed". Watch bit 0 specifically."""
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
    print(f"[{label}] Bit 0 (ARM) = {flags & 1}")
    return flags

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

bridge.start_rc_override()
print("\nRC stream started. Waiting 2 seconds for it to stabilize...")
time.sleep(2)

print("\n--- BEFORE arm (stream already running) ---")
before = read_flags(bridge, "before")

print("\nSending ARM (throttle stays at minimum, safe default)...")
bridge.arm()
time.sleep(2)  # give FC time to actually process the arm

print("\n--- AFTER arm() ---")
after = read_flags(bridge, "after")

if before is not None and after is not None:
    changed = before ^ after
    print(f"\nBits that changed: {changed:032b}")
    if changed & 1:
        print("  Bit 0 (ARM) flipped — this is the real ARM flag!")
    else:
        print("  Bit 0 did NOT flip — something is wrong.")

print("\nSending DISARM...")
bridge.disarm()
time.sleep(2)

print("\n--- AFTER disarm() ---")
read_flags(bridge, "after-disarm")

bridge.stop_rc_override()
bridge.stop()
print("\nDone.")

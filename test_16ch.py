"""Hypothesis test: does sending a full 16-channel MSP_SET_RAW_RC frame (instead
of 8) resolve ARMING_DISABLED_RC_LINK? Props OFF. LiPo disconnected is fine."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200
MSP_STATUS = 101

def read_flags(bridge, label):
    raw = bridge._msp_request(MSP_STATUS, timeout=1.0)
    if raw is None or len(raw) < 11:
        print(f"[{label}] No/short response")
        return None
    cycle_time, i2c_errors, sensors, flags, profile = struct.unpack("<HHHIB", raw[:11])
    print(f"[{label}] Bit 0 (ARM) = {flags & 1}  (full flags: {flags:032b})")
    return flags

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

# 16 channels: Roll, Pitch, Yaw, Throttle, then AUX1-12, all neutral except AUX1=disarmed
channels_16 = [1500, 1500, 1500, 1000, 1000] + [1500] * 11
print(f"\nStreaming 16 channels manually at 20Hz for 2s (disarmed): {channels_16}")

stop_at = time.monotonic() + 2.0
while time.monotonic() < stop_at:
    payload = struct.pack("<16H", *channels_16)
    bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)
    time.sleep(0.05)

print("\n--- BEFORE arm (16ch stream active) ---")
read_flags(bridge, "before")

print("\nSending ARM via 16-channel frame (AUX1 index4 -> 1800)...")
channels_16[4] = 1800
stop_at = time.monotonic() + 2.0
while time.monotonic() < stop_at:
    payload = struct.pack("<16H", *channels_16)
    bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)
    time.sleep(0.05)

print("\n--- AFTER arm (16ch) ---")
read_flags(bridge, "after")

print("\nDisarming (AUX1 -> 1000), sending a few safe frames...")
channels_16[4] = 1000
for _ in range(10):
    payload = struct.pack("<16H", *channels_16)
    bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)
    time.sleep(0.05)

bridge.stop()
print("\nDone.")

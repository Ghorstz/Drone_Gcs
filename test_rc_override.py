"""Standalone bench test for MSP RC override. Props OFF. LiPo disconnected is fine --
FC runs on USB power for this test. Self-verifies by reading RC channels back
from the FC itself (MSP_RC = 105), since INAV Configurator can't be connected
to the same USB port the Pi is using."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_RC = 105  # standard MSP "read RC channels" command

def read_channels(bridge, label):
    raw = bridge._msp_request(MSP_RC, timeout=1.0)
    if raw is None:
        print(f"[{label}] No response from FC to MSP_RC request!")
        return None
    n = len(raw) // 2
    chans = struct.unpack(f"<{n}H", raw[:n * 2])
    print(f"[{label}] FC reports {n} channels: {chans}")
    if n > 4:
        print(f"[{label}] CH5/AUX1 (index 4) = {chans[4]}")
    return chans

bridge = InavBridge()
bridge.start()

print("Waiting for connection...")
time.sleep(3)

print("\n--- BEFORE override started ---")
read_channels(bridge, "before-override")

print("\nStarting RC override stream (safe/disarmed values)...")
bridge.start_rc_override()
time.sleep(1.5)  # let a few packets actually go out

print("\n--- AFTER override started, BEFORE arm ---")
read_channels(bridge, "disarmed")

print("\nSending ARM...")
bridge.arm()
time.sleep(1.5)  # let a few packets with the new value go out

print("\n--- AFTER arm() called ---")
read_channels(bridge, "armed")

print("\nSending DISARM...")
bridge.disarm()
time.sleep(1.5)

print("\n--- AFTER disarm() called ---")
read_channels(bridge, "disarmed-again")

print("\nStopping override + bridge...")
bridge.stop_rc_override()
bridge.stop()
print("Done.")

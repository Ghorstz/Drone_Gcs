"""Read-only bench test: check if MSP_STATUS (cmd 101) reports an ARMED flag.
Props OFF, LiPo disconnected is fine (FC on USB power)."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_STATUS = 101

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

raw = bridge._msp_request(MSP_STATUS, timeout=1.0)
if raw is None:
    print("No response to MSP_STATUS!")
else:
    print(f"Raw bytes ({len(raw)}): {raw.hex()}")
    # MSP_STATUS classic layout: cycleTime(2) i2cErrors(2) sensors(2) flightModeFlags(4) profile(1)
    if len(raw) >= 11:
        cycle_time, i2c_errors, sensors, flags, profile = struct.unpack("<HHHIB", raw[:11])
        print(f"cycleTime={cycle_time} i2cErrors={i2c_errors} sensors={sensors:016b}")
        print(f"flightModeFlags={flags:032b} profile={profile}")
        print(f"Bit 0 of flags (commonly ARM) = {flags & 1}")

bridge.stop()
print("Done.")

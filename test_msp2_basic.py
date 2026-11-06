"""Bench test: MSP v2 native request for MSP2_INAV_STATUS (0x2000).
Verifies the new _msp2_request method against real hardware before
using it in any arm-cycle test. Props OFF. LiPo disconnected. FC USB-only."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP2_INAV_STATUS = 0x2000

bridge = InavBridge()
bridge._open()
print(f"Connected: {bridge.connected}")

print("Sending MSP2_INAV_STATUS request...")
raw = bridge._msp2_request(MSP2_INAV_STATUS, timeout=1.0)

if raw is None:
    print("No reply / CRC mismatch / timeout.")
else:
    print(f"Got {len(raw)} bytes raw: {raw.hex()}")

bridge._close()
print("Done.")

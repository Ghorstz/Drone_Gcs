"""Read MSP_STATUS_EX (150) arming-disable flags in 3 states:
  A: no RC stream at all (expect RC_LINK-related bit set)
  B: RC stream running, AUX1 low
  C: RC stream running, AUX1 high (arm attempt)
Props OFF. LiPo disconnected. USB power only."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200
MSP_STATUS_EX = 150

def read_status_ex(bridge, label):
    raw = bridge._msp_request(MSP_STATUS_EX, timeout=1.0)
    if raw is None:
        print(f"[{label}] No response to MSP_STATUS_EX (150)")
        return None
    print(f"[{label}] payload {len(raw)} bytes: {raw.hex()}")
    if len(raw) >= 15:
        cycle, i2c, sensors, boxes, profile, arm_flags = struct.unpack("<HHHIBI", raw[:15])
        print(f"[{label}] boxes={boxes:032b}  bit0(ARM)={boxes & 1}")
        print(f"[{label}] armingDisableFlags={arm_flags:032b} (decimal {arm_flags})")
        return arm_flags
    return None

bridge = InavBridge()
bridge._open()
time.sleep(1)

print("\n--- STATE A: no RC stream ---")
flags_a = read_status_ex(bridge, "A")

# 16 channels: Roll, Pitch, Yaw, Throttle, AUX1..AUX12
channels = [1500, 1500, 1500, 1000, 1000] + [1500] * 11

print("\n--- STATE B: streaming 16ch, AUX1 low ---")
stop_at = time.monotonic() + 3.0
while time.monotonic() < stop_at:
    bridge._msp_send(MSP_SET_RAW_RC, struct.pack("<16H", *channels), timeout=0.2)
    time.sleep(0.05)
flags_b = read_status_ex(bridge, "B")

channels[4] = 1800
print("\n--- STATE C: streaming, AUX1 high (arm attempt) ---")
stop_at = time.monotonic() + 3.0
while time.monotonic() < stop_at:
    bridge._msp_send(MSP_SET_RAW_RC, struct.pack("<16H", *channels), timeout=0.2)
    time.sleep(0.05)
flags_c = read_status_ex(bridge, "C")

if flags_a is not None and flags_b is not None:
    print(f"\nBits cleared between A and B: {flags_a & ~flags_b:032b}")
if flags_b is not None and flags_c is not None:
    print(f"Bits changed between B and C: {flags_b ^ flags_c:032b}")

channels[4] = 1000
for _ in range(10):
    bridge._msp_send(MSP_SET_RAW_RC, struct.pack("<16H", *channels), timeout=0.2)
    time.sleep(0.05)

bridge._close()
print("\nDone.")

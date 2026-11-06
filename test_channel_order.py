"""Diagnostic: determine the FC's real MSP_SET_RAW_RC channel order by sending
4 distinct, non-repeating values to roll/pitch/throttle/yaw and reading back
via MSP_RC. Props OFF. LiPo disconnected."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_RC = 105

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

bridge.start_rc_override()
time.sleep(0.5)

print("\nSending distinct values: roll=1300, pitch=1400, throttle=1600, yaw=1700")
bridge.manual_control(roll=1300, pitch=1400, throttle=1600, yaw=1700)
time.sleep(1.5)

raw = bridge._msp_request(MSP_RC, timeout=1.0)
n = len(raw) // 2
chans = struct.unpack(f"<{n}H", raw[:n * 2])
print(f"\nFC reports back: {chans[:4]}")
print(f"  index0 = {chans[0]}  (we sent roll=1300)")
print(f"  index1 = {chans[1]}  (we sent pitch=1400)")
print(f"  index2 = {chans[2]}  (we sent throttle=1600)")
print(f"  index3 = {chans[3]}  (we sent yaw=1700)")

bridge.stop_rc_override()
bridge.stop()
print("\nDone. Compare which index shows 1600 (that tells us where throttle really lands)")
print("and which index shows 1700 (that tells us where yaw really lands).")

"""
Resolve a channel-mapping contradiction: manual_control()'s comment claims
index2=YAW/index3=THROTTLE, but INAV's default rcmap={0,1,3,2} (source-
confirmed, rx.c) implies the opposite: logical THROTTLE reads wire index 2,
logical YAW reads wire index 3. Today's fast-RC test data is consistent with
the SOURCE claim, not the bridge's comment -- this script settles it directly
using the real production bridge interface + full 32-bit MSP2_INAV_STATUS.

Props OFF. LiPo disconnected. FC USB-only. ARM channel (ch5) held OFF (1000)
throughout -- no arming attempted, this only checks the THROTTLE flag.
"""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP2_INAV_STATUS = 0x2000
THROTTLE_BIT = 19

def decode_throttle(armf):
    return bool(armf & (1 << THROTTLE_BIT))

def read_full_status(bridge):
    raw = bridge._msp2_request(MSP2_INAV_STATUS, timeout=0.3)
    if raw is None or len(raw) < 13:
        return None
    _, _, _, _, _, armf = struct.unpack_from("<HHHHBI", raw, 0)
    return armf

bridge = InavBridge()
bridge.start()
time.sleep(1.0)
bridge.start_rc_override()

def run_case(label, roll, pitch, yaw, throttle, duration=4.0):
    print(f"\n--- Case: {label} (roll={roll} pitch={pitch} yaw={yaw} throttle={throttle}) ---")
    bridge.manual_control(roll=roll, pitch=pitch, yaw=yaw, throttle=throttle)
    t0 = time.monotonic()
    while time.monotonic() - t0 < duration:
        armf = read_full_status(bridge)
        if armf is not None:
            t = time.monotonic() - t0
            print(f"  t={t:4.1f}s  THROTTLE_flag_active={decode_throttle(armf)}  armingFlags=0x{armf:08x}")
        time.sleep(0.5)

# Case A: throttle genuinely low (1000), yaw centered (1500) -- per manual_control()'s
# documented intent. If source-derived mapping is right, THROTTLE flag should STAY SET
# here (because real wire-index2 = yaw's old slot... see case B for the cross-check).
run_case("A: throttle=1000 (intended low), yaw=1500 (intended center)",
          roll=1500, pitch=1500, yaw=1500, throttle=1000)

# Case B: swap -- send what would be "low" on the OTHER mapping assumption.
# If source rcmap is correct (logical THROTTLE <- wire index2), then to make the FC's
# real throttle read LOW we need wire index2 low. manual_control() puts `yaw` at index2.
# So forcing yaw=1000 here tests whether THAT is what actually clears the FC's throttle flag.
run_case("B: yaw=1000 (testing if THIS clears real THROTTLE per source rcmap), throttle=1500",
          roll=1500, pitch=1500, yaw=1000, throttle=1500)

bridge.stop_rc_override()
bridge.stop()
time.sleep(0.5)
print("\nDone.")

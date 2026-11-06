"""
Standalone verification of the new InavBridge.get_arming_flags()/is_armed()
methods (Fix B, part 1), before wiring them into main_backend.py's
ARM/DISARM confirmation path.

SAFETY: LiPo DISCONNECTED. Props OFF. FC via USB only.
"""
import sys
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

FLAG_NAMES = {
    2: "ARMED", 3: "WAS_EVER_ARMED", 18: "RC_LINK", 19: "THROTTLE",
}

def decode(armf):
    return [name for bit, name in FLAG_NAMES.items() if armf & (1 << bit)]

bridge = InavBridge()
bridge.start()
time.sleep(1.0)
bridge.start_rc_override()

print("Holding safe idle (throttle=1000, ARM off)...")
bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
with bridge.rc_lock:
    bridge.rc_channels[4] = 1000
time.sleep(2.0)

print("\n--- Testing get_arming_flags() / is_armed() while DISARMED ---")
for i in range(5):
    armf = bridge.get_arming_flags()
    armed = bridge.is_armed()
    if armf is None:
        print(f"  [{i}] get_arming_flags() returned None (read failed)")
    else:
        print(f"  [{i}] armingFlags=0x{armf:08x}  active={decode(armf)}  is_armed()={armed}")
    time.sleep(0.4)

print("\nWaiting for clean baseline before arm test...")
t0 = time.monotonic()
clean = False
while time.monotonic() - t0 < 10:
    armf = bridge.get_arming_flags()
    if armf is not None and not decode(armf):
        clean = True
        break
    time.sleep(0.4)

if not clean:
    print(">>> Never reached clean baseline, aborting arm test.")
else:
    print(">>> Clean baseline confirmed. Arming via AUX1...")
    with bridge.rc_lock:
        bridge.rc_channels[4] = 1800
    time.sleep(1.0)
    print("\n--- Testing get_arming_flags() / is_armed() while ARMED ---")
    for i in range(5):
        armf = bridge.get_arming_flags()
        armed = bridge.is_armed()
        print(f"  [{i}] armingFlags=0x{armf:08x}  is_armed()={armed}" if armf is not None
              else f"  [{i}] read failed")
        time.sleep(0.4)
    print("\nDisarming...")
    with bridge.rc_lock:
        bridge.rc_channels[4] = 1000
    time.sleep(1.0)
    armed = bridge.is_armed()
    print(f"After disarm: is_armed()={armed}")

bridge.manual_control(roll=1500, pitch=1500, throttle=1000, yaw=1500)
time.sleep(0.3)
bridge.stop_rc_override()
bridge.stop()
time.sleep(0.5)
print("\nDone.")

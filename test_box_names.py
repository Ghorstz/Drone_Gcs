"""Read-only: ask the FC directly which flightModeFlags bit corresponds to which
named mode (e.g. ARM), instead of guessing bit positions. Props OFF, LiPo
disconnected is fine (USB power)."""
import sys
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_BOXNAMES = 116
MSP_BOXIDS = 119

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

raw_names = bridge._msp_request(MSP_BOXNAMES, timeout=1.0)
raw_ids = bridge._msp_request(MSP_BOXIDS, timeout=1.0)

if raw_names is None:
    print("No response to MSP_BOXNAMES!")
else:
    names = raw_names.decode("ascii", errors="replace").split(";")
    names = [n for n in names if n]  # drop trailing empty from final ';'
    print(f"\n{len(names)} box names, in bit order (bit 0 = first entry):")
    for i, name in enumerate(names):
        print(f"  bit {i}: {name}")

if raw_ids is None:
    print("\nNo response to MSP_BOXIDS!")
else:
    ids = list(raw_ids)
    print(f"\n{len(ids)} box IDs, same order: {ids}")

bridge.stop()
print("\nDone.")

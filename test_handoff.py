"""Hold an arm attempt via MSP RC override, then release the serial port cleanly
so Configurator can reconnect and show the live plain-English arming flags.
Props OFF. LiPo disconnected."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200

bridge = InavBridge()
bridge._open()
time.sleep(1)

channels = [1500, 1500, 1500, 1000, 1800] + [1500] * 11  # AUX1 high = arm attempt
print("Streaming ARM attempt for 5 seconds...")
stop_at = time.monotonic() + 5.0
while time.monotonic() < stop_at:
    bridge._msp_send(MSP_SET_RAW_RC, struct.pack("<16H", *channels), timeout=0.2)
    time.sleep(0.05)

print("Releasing serial port NOW. Quickly switch to Configurator and connect!")
bridge._close()
print("Port released. You have ~15 seconds before this would matter (script exiting).")
time.sleep(15)
print("Done.")

"""Diagnose exactly why MSP_BOXNAMES failed: no reply at all, vs explicit
error reply, vs malformed data. Read-only, props off, LiPo disconnected fine."""
import sys
import time
sys.path.insert(0, "backend")
import inav_bridge
from inav_bridge import InavBridge

MSP_BOXNAMES = 116

bridge = InavBridge()
bridge.start()
print("Waiting for connection...")
time.sleep(3)

ser = bridge.ser
with bridge.write_lock:
    ser.reset_input_buffer()
    ser.write(bytes([0x24, 0x4D, 0x3C, 0x00, MSP_BOXNAMES, MSP_BOXNAMES]))
    time.sleep(0.5)
    raw = ser.read(ser.in_waiting or 1)

print(f"Raw bytes received ({len(raw)}): {raw}")
print(f"Hex: {raw.hex()}")

bridge.stop()
print("Done.")

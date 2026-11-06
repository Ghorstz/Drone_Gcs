"""Isolate RC override from telemetry polling entirely. Does NOT call bridge.start()
(which would launch the competing telemetry thread) -- opens serial directly and
streams RC in this single thread/loop, measuring real achieved rate.
Props OFF. LiPo disconnected is fine."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200
MSP_STATUS = 101

bridge = InavBridge()
bridge._open()  # direct handshake, no background telemetry thread started
print(f"Connected: {bridge.connected}")

channels = [1500, 1500, 1500, 1000, 1000, 1500, 1500, 1500]

def send_frame():
    payload = struct.pack("<8H", *channels)
    return bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.5)

def read_arm_bit(label):
    raw = bridge._msp_request(MSP_STATUS, timeout=1.0)
    if raw is None or len(raw) < 11:
        print(f"[{label}] No/short response")
        return None
    _, _, _, flags, _ = struct.unpack("<HHHIB", raw[:11])
    print(f"[{label}] Bit 0 (ARM) = {flags & 1}")
    return flags & 1

print("\nStreaming RC (disarmed) for 3s, counting successful acks...")
ok_count = 0
total = 0
stop_at = time.monotonic() + 3.0
while time.monotonic() < stop_at:
    total += 1
    if send_frame():
        ok_count += 1
    time.sleep(0.05)
elapsed = 3.0
print(f"Achieved: {ok_count}/{total} acked frames in {elapsed}s => ~{ok_count/elapsed:.1f} Hz successful")

read_arm_bit("before-arm")

print("\nSending ARM (AUX1 -> 1800), streaming for 3s...")
channels[4] = 1800
stop_at = time.monotonic() + 3.0
while time.monotonic() < stop_at:
    send_frame()
    time.sleep(0.05)

read_arm_bit("after-arm")

print("\nDisarming...")
channels[4] = 1000
for _ in range(10):
    send_frame()
    time.sleep(0.05)

bridge._close()
print("\nDone.")

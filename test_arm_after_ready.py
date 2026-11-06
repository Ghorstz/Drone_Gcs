"""Wait for a fully clean arming-flag state (0x0000) while disarmed,
THEN toggle arm switch. Props OFF. LiPo disconnected. FC USB-only."""
import sys
import struct
import time
sys.path.insert(0, "backend")
from inav_bridge import InavBridge

MSP_SET_RAW_RC = 200
MSP_STATUS_EX = 150

bridge = InavBridge()
bridge._open()
print(f"Connected: {bridge.connected}")

def send_frame(ch5):
    channels = [1500, 1500, 1500, 1000, ch5, 1500, 1500, 1500]
    payload = struct.pack("<8H", *channels)
    return bridge._msp_send(MSP_SET_RAW_RC, payload, timeout=0.2)

def read_status_ex():
    raw = bridge._msp_request(MSP_STATUS_EX, timeout=0.5)
    if raw is None or len(raw) != 16:
        return None
    cyc, i2c, sens, box, prof, load, armf, accf = struct.unpack("<HHHIBHHB", raw)
    return box, armf

print("Phase 1: holding DISARM (CH5=1000), waiting for armingFlags_lo16 == 0x0000...")
t0 = time.monotonic()
clean_streak = 0
ready = False
while time.monotonic() - t0 < 20:
    t = time.monotonic() - t0
    send_frame(1000)
    st = read_status_ex()
    if st:
        box, armf = st
        print(f"t={t:4.1f}s  armingFlags_lo16={armf:#06x}")
        if armf == 0:
            clean_streak += 1
        else:
            clean_streak = 0
        if clean_streak >= 3:
            ready = True
            break
    time.sleep(0.3)

if not ready:
    print("Never reached clean 0x0000 state within 20s. Stopping here.")
    send_frame(1000)
    bridge._close()
    sys.exit(0)

print(">>> Clean state confirmed. Holding disarm 1s more, then ARM toggle...")
t1 = time.monotonic()
while time.monotonic() - t1 < 1.0:
    send_frame(1000)
    time.sleep(0.1)

print(">>> ARM (CH5=1800) now")
t2 = time.monotonic()
while time.monotonic() - t2 < 6:
    t = time.monotonic() - t2
    send_frame(1800)
    st = read_status_ex()
    if st:
        box, armf = st
        print(f"t={t:4.1f}s  ARMbox(bit0)={box & 1}  armingFlags_lo16={armf:#06x}  ARMED(bit2)={(armf >> 2) & 1}")
    time.sleep(0.3)

send_frame(1000)
bridge._close()
print("Done.")

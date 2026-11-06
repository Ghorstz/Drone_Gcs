"""Stream RC from boot (before failsafe can latch), attempt arm at t=3s,
poll correctly-parsed MSP_STATUS_EX every 0.5s for 15s.
Props OFF. LiPo disconnected. FC USB-only."""
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

print("Streaming neutral RC immediately...")
t0 = time.monotonic()
armed_sent = False
next_poll = 0.0
while time.monotonic() - t0 < 15:
    t = time.monotonic() - t0
    ch5 = 1800 if t > 3 else 1000
    send_frame(ch5)
    if not armed_sent and t > 3:
        armed_sent = True
        print(">>> ARM attempt (CH5=1800) from t=3s")
    if t >= next_poll:
        st = read_status_ex()
        if st:
            box, armf = st
            print(f"t={t:4.1f}s  ARMbox(bit0)={box & 1}  FAILSAFEbox(bit26)={(box >> 26) & 1}  "
                  f"armingFlags_lo16={armf:#06x}  ARMED(bit2)={(armf >> 2) & 1}")
        next_poll += 0.5
    time.sleep(0.05)

send_frame(1000)
bridge._close()
print("Done.")

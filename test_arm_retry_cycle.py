"""Option 1: after reaching a clean baseline, cycle ARM switch OFF->ON
5 times with tight RC streaming (20ms), to see if the ARM_SWITCH latch
is consistent (every time) or intermittent (sometimes succeeds).
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

def stream_for(seconds, ch5, poll_times=None, label=""):
    t0 = time.monotonic()
    poll_times = poll_times or []
    done_polls = set()
    while time.monotonic() - t0 < seconds:
        t = time.monotonic() - t0
        send_frame(ch5)
        for pt in poll_times:
            if pt not in done_polls and t >= pt:
                done_polls.add(pt)
                st = read_status_ex()
                if st:
                    box, armf = st
                    print(f"  [{label} t={t:4.2f}s] ARMbox={box&1} armingFlags_lo16={armf:#06x} ARMED={(armf>>2)&1}")
        time.sleep(0.02)  # 20ms => ~50Hz

print("Phase 1: holding DISARM, waiting for clean 0x0000 baseline...")
t0 = time.monotonic()
clean_streak = 0
ready = False
while time.monotonic() - t0 < 20:
    send_frame(1000)
    st = read_status_ex()
    if st:
        box, armf = st
        if armf == 0:
            clean_streak += 1
        else:
            clean_streak = 0
        if clean_streak >= 3:
            ready = True
            break
    time.sleep(0.3)

if not ready:
    print("Never reached clean baseline. Stopping.")
    send_frame(1000)
    bridge._close()
    sys.exit(0)

print(">>> Clean baseline confirmed.\n")

for cycle in range(1, 6):
    print(f"--- Cycle {cycle}/5 ---")
    print("  Disarm hold (0.5s)...")
    stream_for(0.5, 1000, poll_times=[0.4], label="disarm")
    print("  ARM (0.8s)...")
    stream_for(0.8, 1800, poll_times=[0.02, 0.1, 0.3, 0.6], label="arm")

print("\nFinal disarm...")
stream_for(0.5, 1000)
bridge._close()
print("Done.")

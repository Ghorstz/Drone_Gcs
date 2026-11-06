#!/usr/bin/env python3
import time, urllib.request
for i in range(8):
    r = urllib.request.urlopen("http://127.0.0.1:8000/video_feed", timeout=5)
    buf = b""
    while True:
        buf += r.read(4096)
        a = buf.find(b"\xff\xd8")
        b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
        if a >= 0 and b >= 0:
            open(f"plant_test/frame_{i}.jpg", "wb").write(buf[a:b + 2])
            break
    r.close()
    print("saved frame", i, flush=True)
    time.sleep(6)

#!/usr/bin/env python3
# Read-only performance logger. Does not touch the FC serial port.
import re, subprocess, time, urllib.request

def cpu_times():
    v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    return sum(v), v[3] + v[4]

def mem_used_pct():
    m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    return 100.0 * (1 - m["MemAvailable"] / m["MemTotal"])

def temp_c():
    out = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True).stdout
    return float(re.search(r"([\d.]+)", out).group(1))

def throttled():
    return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout.strip()

def health_ms():
    t = time.perf_counter()
    urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3).read()
    return (time.perf_counter() - t) * 1000

def stream_fps(seconds=10):
    frames, tail = 0, b""
    end = time.monotonic() + seconds
    r = urllib.request.urlopen("http://127.0.0.1:8000/video_feed", timeout=5)
    while time.monotonic() < end:
        chunk = r.read(4096)
        if not chunk:
            break
        data = tail + chunk
        frames += data.count(b"\xff\xd8")
        tail = data[-1:]
    r.close()
    return frames / seconds

print("Measuring stream rate for 10 s (approximate, counts JPEG markers)...")
fps = stream_fps()
print(f"stream_fps_approx={fps:.1f}")

rows = []
t0 = time.monotonic()
prev = cpu_times()
with open("perf_log.csv", "w") as f:
    f.write("elapsed_s,cpu_pct,mem_pct,temp_c,health_ms\n")
    while time.monotonic() - t0 < 90:
        time.sleep(2)
        cur = cpu_times()
        dt, di = cur[0] - prev[0], cur[1] - prev[1]
        prev = cur
        cpu = 100.0 * (1 - di / dt) if dt else 0.0
        row = (time.monotonic() - t0, cpu, mem_used_pct(), temp_c(), health_ms())
        rows.append(row)
        line = "%.0f,%.1f,%.1f,%.1f,%.1f" % row
        f.write(line + "\n"); f.flush()
        print(line)

def col(i): return [r[i] for r in rows]
for name, i in (("cpu_pct", 1), ("mem_pct", 2), ("temp_c", 3), ("health_ms", 4)):
    c = col(i)
    print(f"{name}: mean={sum(c)/len(c):.1f} max={max(c):.1f}")
print("stream_fps_approx:", f"{fps:.1f}")
print("throttled:", throttled())

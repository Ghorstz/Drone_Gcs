#!/usr/bin/env python3
import asyncio, json, urllib.request, urllib.error
BASE = "127.0.0.1:8000"

def http(path, method="GET"):
    req = urllib.request.Request(f"http://{BASE}{path}", method=method, data=b"" if method == "POST" else None)
    try:
        r = urllib.request.urlopen(req, timeout=3)
        r.read(200)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"error {type(e).__name__}"

async def ws_test(first_msg):
    import websockets
    async with websockets.connect(f"ws://{BASE}/ws") as ws:
        if first_msg is not None:
            await ws.send(json.dumps(first_msg))
        got = []
        try:
            while len(got) < 3:
                got.append(json.loads(await asyncio.wait_for(ws.recv(), 1.5)).get("type"))
        except Exception:
            pass
        return got

async def main():
    rows = []
    rows.append(("WS no message: types received", await ws_test(None)))
    rows.append(("WS wrong token", await ws_test({"type": "auth", "token": "wrong-token-123456"})))
    rows.append(("WS manual before login", await ws_test({"type": "manual", "x": 0, "y": 0, "z": 900, "r": 0})))
    for p, m in (("/api/health", "GET"), ("/api/sync/status", "GET"), ("/api/sync", "POST"), ("/video_feed", "GET")):
        rows.append((f"{m} {p} no token: status", http(p, m)))
    for k, v in rows:
        print(f"{k:45s} {v}")

asyncio.run(main())

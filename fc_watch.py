#!/usr/bin/env python3
# Read-only: polls MSP2_INAV_STATUS and prints armingFlags. Sends no RC, no commands.
import serial, struct, time

PORT = "/dev/ttyACM0"

def crc8(crc, data):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def read_flags(ser):
    header = struct.pack("<BHH", 0, 0x2000, 0)
    ser.reset_input_buffer()
    ser.write(b"$X<" + header + bytes([crc8(0, header)]))
    deadline = time.monotonic() + 0.4
    buf = bytearray()
    while time.monotonic() < deadline:
        buf += ser.read(ser.in_waiting or 1)
        i = buf.find(b"$X")
        if i >= 0 and len(buf) >= i + 8:
            _, cmd, size = struct.unpack("<BHH", bytes(buf[i + 3:i + 8]))
            end = i + 8 + size + 1
            if len(buf) >= end and cmd == 0x2000 and size >= 13:
                payload = bytes(buf[i + 8:i + 8 + size])
                return struct.unpack_from("<HHHHBI", payload, 0)[5]
    return None

ser = serial.Serial(PORT, 115200, timeout=0.05)
t0 = time.monotonic()
while time.monotonic() - t0 < 45:
    f = read_flags(ser)
    t = time.monotonic() - t0
    if f is None:
        print(f"{t:5.1f}s  no reply")
    else:
        print(f"{t:5.1f}s  armed={bool(f & 4)}  armingFlags=0x{f:08x}")
    time.sleep(0.5)

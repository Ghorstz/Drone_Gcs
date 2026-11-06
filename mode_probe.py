#!/usr/bin/env python3
# Read-only: prints the FC's mode list and the raw status reply. Sends no RC, no commands.
import serial, struct, time

PORT = "/dev/ttyACM0"

def crc8(crc, data):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def v1(ser, cmd):
    ser.reset_input_buffer()
    ser.write(bytes([0x24, 0x4D, 0x3C, 0x00, cmd, cmd]))
    end = time.monotonic() + 0.6
    buf = bytearray()
    while time.monotonic() < end:
        buf += ser.read(ser.in_waiting or 1)
        i = buf.find(b"$M>")
        if i >= 0 and len(buf) >= i + 5:
            size = buf[i + 3]
            if len(buf) >= i + 6 + size and buf[i + 4] == cmd:
                return bytes(buf[i + 5:i + 5 + size])
    return None

def v2(ser, cmd):
    header = struct.pack("<BHH", 0, cmd, 0)
    ser.reset_input_buffer()
    ser.write(b"$X<" + header + bytes([crc8(0, header)]))
    end = time.monotonic() + 0.6
    buf = bytearray()
    while time.monotonic() < end:
        buf += ser.read(ser.in_waiting or 1)
        i = buf.find(b"$X")
        if i >= 0 and len(buf) >= i + 8:
            _, c, size = struct.unpack("<BHH", bytes(buf[i + 3:i + 8]))
            if len(buf) >= i + 9 + size and c == cmd:
                return bytes(buf[i + 8:i + 8 + size])
    return None

ser = serial.Serial(PORT, 115200, timeout=0.05)
names = v1(ser, 116)
ids = v1(ser, 119)
status = v2(ser, 0x2000)
print("BOXNAMES:", names.decode(errors="replace") if names else None)
print("BOXIDS  :", list(ids) if ids else None)
print("STATUS len:", None if status is None else len(status))
print("STATUS hex:", None if status is None else status.hex())
if status and len(status) >= 13:
    print("armingFlags: 0x%08x" % struct.unpack_from("<I", status, 9)[0])
    print("bytes after flags:", status[13:].hex())

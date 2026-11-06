path = "backend/inav_bridge.py"
with open(path, "r") as f:
    src = f.read()

if "confirmed via MSP_RC readback" in src:
    raise SystemExit("ERROR: fix already appears to be applied. Aborting to avoid double-patch.")

# --- Fix 1: __init__ default rc_channels array ---
old_init = "        self.rc_lock = threading.Lock()\n        self.rc_channels = [1500, 1500, 1000, 1500, 1000, 1000, 1000, 1000]\n        self.rc_enabled = False\n        self.rc_thread: Optional[threading.Thread] = None\n"
new_init = '''        self.rc_lock = threading.Lock()
        # Channel order confirmed via MSP_RC readback test: Roll, Pitch, YAW, THROTTLE
        # (standard MSP wire order), NOT Roll, Pitch, Throttle, Yaw as first assumed.
        self.rc_channels = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
        self.rc_enabled = False
        self.rc_thread: Optional[threading.Thread] = None
'''
if old_init not in src:
    raise SystemExit("ERROR: __init__ rc_channels block not found exactly as expected. Aborting.")
src = src.replace(old_init, new_init, 1)

# --- Fix 2: stop_rc_override safe-reset array ---
old_stop = "        with self.rc_lock:\n            self.rc_channels = [1500, 1500, 1000, 1500, 1000, 1000, 1000, 1000]\n        time.sleep(0.1)"
new_stop = "        with self.rc_lock:\n            self.rc_channels = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]\n        time.sleep(0.1)"
if old_stop not in src:
    raise SystemExit("ERROR: stop_rc_override block not found exactly as expected. Aborting.")
src = src.replace(old_stop, new_stop, 1)

# --- Fix 3: manual_control channel assignment ---
old_manual = '''    def manual_control(self, roll: int = 1500, pitch: int = 1500,
                        throttle: int = 1000, yaw: int = 1500):
        with self.rc_lock:
            self.rc_channels[0] = roll
            self.rc_channels[1] = pitch
            self.rc_channels[2] = throttle
            self.rc_channels[3] = yaw
'''
new_manual = '''    def manual_control(self, roll: int = 1500, pitch: int = 1500,
                        throttle: int = 1000, yaw: int = 1500):
        # Channel order confirmed via MSP_RC readback test: index2=YAW, index3=THROTTLE
        # (standard MSP wire order is Roll, Pitch, Yaw, Throttle).
        with self.rc_lock:
            self.rc_channels[0] = roll
            self.rc_channels[1] = pitch
            self.rc_channels[2] = yaw
            self.rc_channels[3] = throttle
'''
if old_manual not in src:
    raise SystemExit("ERROR: manual_control block not found exactly as expected. Aborting.")
src = src.replace(old_manual, new_manual, 1)

with open(path, "w") as f:
    f.write(src)

print("All 3 channel-order fixes applied successfully.")

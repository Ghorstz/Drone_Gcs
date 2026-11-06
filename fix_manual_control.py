"""Fix manual_control() channel-mapping bug: swaps yaw/throttle assignment
to match INAV's confirmed active rcmap={0,1,3,2} (AETR). Empirically
verified via test_manual_control_mapping.py -- see inav_bridge.py.bak
for pre-fix version. Backs up again before writing, verifies via ast.parse
before and after."""
import ast
import shutil
import time

path = "backend/inav_bridge.py"

# Extra safety backup specific to this edit, on top of the manual one already made
backup_path = f"{path}.bak.manualcontrol.{int(time.time())}"
shutil.copy(path, backup_path)
print(f"Backed up to {backup_path}")

with open(path, "r") as f:
    src = f.read()

# Verify current file parses before touching it
ast.parse(src)

old_block = '''        # Channel order confirmed via MSP_RC readback test: index2=YAW, index3=THROTTLE
        # (standard MSP wire order is Roll, Pitch, Yaw, Throttle).
        with self.rc_lock:
            self.rc_channels[0] = roll
            self.rc_channels[1] = pitch
            self.rc_channels[2] = yaw
            self.rc_channels[3] = throttle'''

new_block = '''        # Channel order CORRECTED (empirically re-verified via full 32-bit
        # MSP2_INAV_STATUS armingFlags, test_manual_control_mapping.py):
        # INAV's active rcmap is the documented default {0,1,3,2} (AETR) --
        # source-confirmed in rx.c/rx.h. Wire index 2 = THROTTLE, wire
        # index 3 = YAW. The prior comment here ("index2=YAW, index3=
        # THROTTLE") was WRONG -- based on an earlier test that was
        # corrupted by the then-undiscovered RC_LINK truncation bug.
        # Confirmed: old mapping left ARMING_DISABLED_THROTTLE permanently
        # set; this mapping produces a clean armingFlags == 0x00000000.
        with self.rc_lock:
            self.rc_channels[0] = roll
            self.rc_channels[1] = pitch
            self.rc_channels[2] = throttle
            self.rc_channels[3] = yaw'''

count = src.count(old_block)
if count != 1:
    print(f"ERROR: expected exactly 1 match, found {count}. Aborting, no changes written.")
    raise SystemExit(1)

new_src = src.replace(old_block, new_block)

# Verify the new source still parses before writing it to disk
ast.parse(new_src)

with open(path, "w") as f:
    f.write(new_src)

print("Patch applied successfully.")
print("Re-parsing final file from disk to confirm...")
with open(path, "r") as f:
    final_src = f.read()
ast.parse(final_src)
print("OK: final file is valid Python.")

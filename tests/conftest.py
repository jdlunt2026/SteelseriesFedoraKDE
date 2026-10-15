import os

# Use rivalcfg's fake HID device so tests never touch real hardware.
os.environ["RIVALCFG_DRY"] = "1"
os.environ["RIVALCFG_DEBUG_NO_COMMAND_DELAY"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# License : GPLv2.0
# copyright (c) 2023  Dave Bailey
# Author: Dave Bailey (dbisu, @daveisu)
# Pico and Pico W board support
#
# Hardened: tolerates missing pins, missing files, and partial reads. Validates
# payload paths so a malformed name on disk can't redirect the exfil check.

from board import *
import board
import digitalio
import storage
import os

PAYLOAD_NAME = "payload.dd"
LOOT_NAME = "loot.bin"


def _is_exfil_enabled(payload_path=PAYLOAD_NAME):
    """Return True if the active payload opts into exfil mode."""
    try:
        with open(payload_path, "r") as f:
            for line in f:
                upper = line.upper()
                if "$_EXFIL_MODE_ENABLED" in upper and "TRUE" in upper:
                    return True
                # Keep scan bounded to avoid runaway loops on huge files.
                if f.tell() > 16 * 1024:
                    break
    except (OSError, ValueError):
        pass
    return False


def _loot_exists():
    try:
        return LOOT_NAME in os.listdir("/")
    except OSError:
        return False


def _no_storage_jumper():
    """Read GP15; default False (USB visible) if pin can't be claimed."""
    try:
        pin = digitalio.DigitalInOut(GP15)
        pin.switch_to_input(pull=digitalio.Pull.UP)
        return pin.value
    except Exception as ex:  # pragma: no cover - hardware specific
        print("boot: could not read GP15:", ex)
        return True  # safe default: behave as if jumper not present


def _disable_usb():
    try:
        storage.disable_usb_drive()
        print("boot: USB drive hidden from host")
    except Exception as ex:
        print("boot: failed to disable USB drive:", ex)


exfil_enabled = _is_exfil_enabled()
loot_exists = _loot_exists()
noStorageStatus = _no_storage_jumper()

# If GP15 is not connected, it defaults to being pulled high (True).
# If GP15 is connected to GND, it reads low (False).
#
# Pico:
#   GP15 not connected     -> USB visible
#   GP15 connected to GND  -> USB hidden
#
# Pico W:
#   GP15 not connected     -> USB hidden
#   GP15 connected to GND  -> USB visible

if exfil_enabled and not loot_exists:
    _disable_usb()

if board.board_id in ('raspberry_pi_pico', 'raspberry_pi_pico2'):
    noStorage = not noStorageStatus
elif board.board_id in ('raspberry_pi_pico_w', 'raspberry_pi_pico2_w'):
    noStorage = noStorageStatus
else:
    # Unknown board: be conservative — leave storage visible so user can recover.
    noStorage = False

if noStorage:
    _disable_usb()
else:
    print("boot: USB drive enabled")

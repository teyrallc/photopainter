#!/usr/bin/env python3
"""
GPIO button controller for Vignette.
Monitors 4 buttons to navigate through generated images.
Adapted from PaperPiAI (https://github.com/dylski/PaperPiAI)

Button mapping (defaults; override with env vars if your board differs):
  A (GPIO 5)  - Display newest image      [VIGNETTE_BTN_A]
  B (GPIO 6)  - Previous image            [VIGNETTE_BTN_B]
  C (GPIO 16) - Next image                [VIGNETTE_BTN_C]
  D (GPIO 26) - Shutdown system           [VIGNETTE_BTN_D]

NOTE: This uses libgpiod v2 (the API of PyPI's `gpiod`, matching the install
instruction below). It runs as a separate helper — the web UI has its own
virtual buttons. GPIO 24 is deliberately NOT used for a button: it is the
panel BUSY line (epdconfig BUSY_PIN=24), so the original Button D=24 conflicted
with the display. Confirm these offsets match your actual board wiring.
"""

import glob
import os
import subprocess
import sys
import time
from datetime import timedelta

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

# Image output directory
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
DISPLAY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_picture.py")


def _pin(env, default):
    try:
        return int(os.environ.get(env, default))
    except ValueError:
        return default


# GPIO button pins (BCM). D moved off 24 (panel BUSY) to a free pin.
BUTTON_A = _pin("VIGNETTE_BTN_A", 5)   # Newest
BUTTON_B = _pin("VIGNETTE_BTN_B", 6)   # Previous
BUTTON_C = _pin("VIGNETTE_BTN_C", 16)  # Next
BUTTON_D = _pin("VIGNETTE_BTN_D", 26)  # Shutdown

BUTTONS = [BUTTON_A, BUTTON_B, BUTTON_C, BUTTON_D]

# Pins claimed by the e-paper panel (epdconfig) — buttons must not overlap these.
_EPD_PINS = {17, 25, 8, 24, 27}  # RST, DC, CS, BUSY, PWR


def get_image_list():
    """Get sorted list of images in output directory."""
    patterns = ["*.png", "*.jpg", "*.jpeg"]
    images = []
    for pattern in patterns:
        images.extend(glob.glob(os.path.join(OUTPUT_DIR, pattern)))
    images.sort(key=os.path.getmtime)  # oldest → newest
    return images


def display_image(image_path):
    """Display an image using display_picture.py."""
    print(f"Displaying: {image_path}")
    venv_python = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "venv", "bin", "python")
    python = venv_python if os.path.exists(venv_python) else sys.executable
    subprocess.run([python, DISPLAY_SCRIPT, image_path])


def _find_gpiochip(gpiod):
    """Find the chip that owns the 40-pin header GPIOs. On the Pi Zero 2 W this
    is gpiochip0 (pinctrl-bcm2835); on a Pi 5 it is gpiochip4 (pinctrl-rp1).
    Hard-coding a single name breaks on the other board, so detect it."""
    override = os.environ.get("VIGNETTE_GPIOCHIP")
    if override:
        return override
    for path in sorted(glob.glob('/dev/gpiochip*')):
        try:
            with gpiod.Chip(path) as chip:
                info = chip.get_info()
                if 'pinctrl' in (info.label or '') and info.num_lines >= 40:
                    return path
        except Exception:
            continue
    return '/dev/gpiochip0'


def main():
    overlap = _EPD_PINS.intersection(BUTTONS)
    if overlap:
        print(f"ERROR: button pins {sorted(overlap)} collide with e-paper pins "
              f"{sorted(_EPD_PINS)}. Reassign via VIGNETTE_BTN_* env vars.")
        sys.exit(1)

    try:
        import gpiod
        from gpiod.line import Edge, Bias
    except ImportError:
        print("Error: gpiod (libgpiod v2) not available. Install with: pip install gpiod")
        print("This script requires GPIO hardware (Raspberry Pi).")
        sys.exit(1)

    chip_path = _find_gpiochip(gpiod)
    print(f"Using GPIO chip: {chip_path}")

    settings = gpiod.LineSettings(
        edge_detection=Edge.FALLING,
        bias=Bias.PULL_UP,
        debounce_period=timedelta(milliseconds=30),
    )
    request = gpiod.request_lines(
        chip_path,
        consumer="vignette",
        config={tuple(BUTTONS): settings},
    )

    current_index = -1  # -1 means newest
    images = get_image_list()

    print("Vignette Button Controller")
    print(f"Found {len(images)} images in {OUTPUT_DIR}")
    print("Buttons: A=Newest  B=Previous  C=Next  D=Shutdown")
    print("Waiting for button press...")

    try:
        while True:
            if not request.wait_edge_events(timedelta(seconds=1)):
                continue
            events = request.read_edge_events()
            if not events:
                continue
            # Act on the most recent press only, so a bounce/mash is one action.
            pin = events[-1].line_offset
            images = get_image_list()  # Refresh list

            if pin == BUTTON_D:
                print("Button D: Shutting down...")
                subprocess.run(["sudo", "shutdown", "-h", "now"])
                return

            if not images:
                print("No images found!")
            elif pin == BUTTON_A:
                print("Button A: Showing newest image")
                current_index = len(images) - 1
                display_image(images[current_index])
            elif pin == BUTTON_B:
                print("Button B: Previous image")
                current_index = (current_index - 1) % len(images) if current_index > 0 else len(images) - 1
                display_image(images[current_index])
            elif pin == BUTTON_C:
                print("Button C: Next image")
                current_index = (current_index + 1) % len(images) if current_index >= 0 else 0
                display_image(images[current_index])

            # Drain edges that queued during the (slow) refresh so a single
            # press doesn't cascade into several image changes.
            while request.wait_edge_events(timedelta(0)):
                request.read_edge_events()

    except KeyboardInterrupt:
        print("\nExiting button controller.")
    finally:
        request.release()


if __name__ == "__main__":
    main()

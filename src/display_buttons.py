#!/usr/bin/env python3
"""
GPIO button controller for Vignette.
Monitors 4 buttons to navigate through generated images.
Adapted from PaperPiAI (https://github.com/dylski/PaperPiAI)

Button mapping:
  A (GPIO 5)  - Display newest image
  B (GPIO 6)  - Previous image
  C (GPIO 16) - Next image
  D (GPIO 24) - Shutdown system
"""

import glob
import os
import subprocess
import sys
import time

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

# Image output directory
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
DISPLAY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_picture.py")

# GPIO button pins
BUTTON_A = 5   # Newest
BUTTON_B = 6   # Previous
BUTTON_C = 16  # Next
BUTTON_D = 24  # Shutdown

BUTTONS = [BUTTON_A, BUTTON_B, BUTTON_C, BUTTON_D]


def get_image_list():
    """Get sorted list of images in output directory."""
    patterns = ["*.png", "*.jpg", "*.jpeg"]
    images = []
    for pattern in patterns:
        images.extend(glob.glob(os.path.join(OUTPUT_DIR, pattern)))
    # Sort by modification time
    images.sort(key=os.path.getmtime)
    return images


def display_image(image_path):
    """Display an image using display_picture.py."""
    print(f"Displaying: {image_path}")
    venv_python = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "venv", "bin", "python")
    python = venv_python if os.path.exists(venv_python) else sys.executable
    subprocess.run([python, DISPLAY_SCRIPT, image_path])


def find_gpio_chip(gpiod):
    """Locate the SoC's GPIO chip.

    The chip number is not stable across Raspberry Pi models — it is
    gpiochip0 on a Pi Zero 2 W and gpiochip4 on a Pi 5 — so hard-coding one
    means the buttons silently do nothing on every other board. Match on the
    driver label instead, which is what actually identifies the SoC banks.
    """
    labels = ("pinctrl-bcm2835", "pinctrl-bcm2711", "pinctrl-rp1", "gpio-brcmstb")
    for number in range(6):
        path = f"/dev/gpiochip{number}"
        if not os.path.exists(path):
            continue
        try:
            chip = gpiod.Chip(path)
        except (OSError, PermissionError):
            continue
        label = getattr(chip, "label", "") or ""
        if any(known in label for known in labels) and chip.num_lines() >= 32:
            print(f"Using {path} ({label})")
            return chip
        chip.close()

    raise RuntimeError(
        "No Raspberry Pi GPIO chip found. Checked /dev/gpiochip0-5. "
        "If this is a Pi, check that the user is in the 'gpio' group.")


def main():
    try:
        import gpiod
    except ImportError:
        print("Error: the libgpiod Python bindings are not available.")
        print("Install them with:  sudo apt-get install -y python3-libgpiod")
        print("NOTE: the PyPI package called 'gpiod' is the incompatible v2 API;")
        print("      this script uses the v1 API that Debian ships.")
        sys.exit(1)

    if not hasattr(gpiod, "LINE_REQ_EV_FALLING_EDGE"):
        print("Error: this is libgpiod v2, whose API differs from the v1 one")
        print("       this script uses. Install Debian's python3-libgpiod and")
        print("       run with the system interpreter, or a venv created with")
        print("       --system-site-packages.")
        sys.exit(1)

    chip = find_gpio_chip(gpiod)
    lines = {}
    for pin in BUTTONS:
        line = chip.get_line(pin)
        line.request(consumer="vignette", type=gpiod.LINE_REQ_EV_FALLING_EDGE)
        lines[pin] = line

    current_index = -1  # -1 means newest
    images = get_image_list()

    print("Vignette Button Controller")
    print(f"Found {len(images)} images in {OUTPUT_DIR}")
    print("Buttons: A=Newest  B=Previous  C=Next  D=Shutdown")
    print("Waiting for button press...")

    try:
        while True:
            for pin, line in lines.items():
                if line.event_wait(sec=0, nsec=100000000):  # 100ms timeout
                    event = line.event_read()
                    images = get_image_list()  # Refresh list

                    if not images:
                        print("No images found!")
                        continue

                    if pin == BUTTON_A:
                        print("Button A: Showing newest image")
                        current_index = len(images) - 1
                        display_image(images[current_index])

                    elif pin == BUTTON_B:
                        print("Button B: Previous image")
                        if current_index <= 0:
                            current_index = len(images) - 1
                        else:
                            current_index -= 1
                        display_image(images[current_index])

                    elif pin == BUTTON_C:
                        print("Button C: Next image")
                        if current_index >= len(images) - 1:
                            current_index = 0
                        else:
                            current_index += 1
                        display_image(images[current_index])

                    elif pin == BUTTON_D:
                        print("Button D: Shutting down...")
                        subprocess.run(["sudo", "shutdown", "-h", "now"])
                        return

                    time.sleep(0.3)  # Debounce

    except KeyboardInterrupt:
        print("\nExiting button controller.")
    finally:
        for line in lines.values():
            line.release()


if __name__ == "__main__":
    main()

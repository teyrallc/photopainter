#!/usr/bin/env python3
"""
GPIO button controller for PhotoPainter.
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


def main():
    try:
        import gpiod
    except ImportError:
        print("Error: gpiod library not available. Install with: pip install gpiod")
        print("This script requires GPIO hardware (Raspberry Pi).")
        sys.exit(1)

    chip = gpiod.Chip('gpiochip4')
    lines = {}
    for pin in BUTTONS:
        line = chip.get_line(pin)
        line.request(consumer="photopainter", type=gpiod.LINE_REQ_EV_FALLING_EDGE)
        lines[pin] = line

    current_index = -1  # -1 means newest
    images = get_image_list()

    print("PhotoPainter Button Controller")
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

#!/usr/bin/env python3
"""
Display an image on the Waveshare 7.3" 6-color e-paper display.
Adapted from PaperPiAI (https://github.com/dylski/PaperPiAI)
Uses Waveshare epd7in3e driver instead of Inky.
"""

import argparse
import os
import sys

# Add lib directory to path for waveshare_epd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import cv2
import numpy as np
from PIL import Image


def load_image(image_path):
    """Load image using OpenCV."""
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Cannot load image: {image_path}")
    return image


def save_image(image_path, image):
    """Save image using OpenCV."""
    cv2.imwrite(image_path, image)


def crop(image, disp_w, disp_h, intelligent=True):
    """
    Intelligently resize and crop image to display proportions.
    Uses saliency detection to shift crop towards visually important areas.
    """
    img_h, img_w, img_c = image.shape
    print(f"Input WxH: {img_w} x {img_h}")

    img_aspect = img_w / img_h
    disp_aspect = disp_w / disp_h

    if img_aspect < disp_aspect:
        # Scale width, crop height
        resize = (disp_w, int(disp_w / img_aspect))
    else:
        # Scale height, crop width
        resize = (int(disp_h * img_aspect), disp_h)

    print(f"Resizing to {resize}")
    image = cv2.resize(image, resize)
    img_h, img_w, img_c = image.shape

    # Cropping offsets
    x_off = int((img_w - disp_w) / 2)
    y_off = int((img_h - disp_h) / 2)

    if intelligent:
        try:
            # Use saliency detection to shift crop towards important regions
            saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
            (success, saliencyMap) = saliency.computeSaliency(image)
            saliencyMap = (saliencyMap * 255).astype("uint8")

            if not x_off:
                # Cropping height
                vert = np.max(saliencyMap, axis=1)
                vert = np.convolve(vert, np.ones(64)/64, "same")
                sal_centre = int(np.argmax(vert))
                img_centre = int(img_h / 2)
                shift_y = max(min(sal_centre - img_centre, y_off), -y_off)
                y_off += shift_y
            else:
                # Cropping width
                horiz = np.max(saliencyMap, axis=0)
                horiz = np.convolve(horiz, np.ones(64)/64, "same")
                sal_centre = int(np.argmax(horiz))
                img_centre = int(img_w / 2)
                shift_x = max(min(sal_centre - img_centre, x_off), -x_off)
                x_off += shift_x
        except Exception as e:
            print(f"Saliency detection failed, using center crop: {e}")

    image = image[y_off:y_off + disp_h, x_off:x_off + disp_w]
    img_h, img_w, img_c = image.shape
    print(f"Cropped WxH: {img_w} x {img_h}")
    return image


def display_on_epaper(image):
    """Display a CV2 image on the Waveshare 7.3" e-paper display."""
    from waveshare_epd import epd7in3e, epdconfig

    # Rotate if portrait
    if image.shape[0] > image.shape[1]:
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

    # Convert BGR to RGB
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # Convert to PIL Image
    pil_image = Image.fromarray(image)

    # Initialize display — init() returns -1 (does not raise) on failure.
    epd = epd7in3e.EPD()
    print("Initializing e-paper display...")
    if epd.init() != 0:
        raise RuntimeError("e-paper init failed")

    try:
        print("Sending image to display...")
        buf = epd.getbuffer(pil_image)
        epd.display(buf)
        print("Entering sleep mode...")
        epd.sleep()
        print("Display updated successfully!")
    finally:
        # Always drop power / close SPI even if getbuffer/display raised, so the
        # panel is never left energized after a partial refresh.
        try:
            epdconfig.module_exit()
        except Exception:
            pass


def quantize_preview(image):
    """
    Quantize image to the panel's 6-color palette (simulates e-paper appearance).
    Returns a PIL Image for preview purposes.
    """
    if len(image.shape) == 3 and image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    pil_image = Image.fromarray(image)

    # Palette must match epd7in3e.getbuffer exactly: black, white, yellow, red,
    # (black filler), blue, green. The 6-color Spectra panel has NO orange, so
    # the old 7-color palette produced a preview the hardware can't reproduce.
    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(
        (0, 0, 0,  255, 255, 255,  255, 255, 0,  255, 0, 0,
         0, 0, 0,  0, 0, 255,  0, 255, 0) + (0, 0, 0) * 249
    )

    image_6color = pil_image.convert("RGB").quantize(palette=pal_image)
    return image_6color.convert("RGB")


def main():
    ap = argparse.ArgumentParser(description="Display image on Waveshare 7.3\" e-paper")
    ap.add_argument("image", help="Input image path")
    ap.add_argument("-o", "--output", default="",
                    help="Save cropped image to this path")
    ap.add_argument("-p", "--portrait", action="store_true",
                    default=False, help="Portrait orientation")
    ap.add_argument("-c", "--centre_crop", action="store_true",
                    default=False, help="Simple centre cropping (no saliency)")
    ap.add_argument("-r", "--resize_only", action="store_true",
                    default=False, help="Simply resize, ignore aspect ratio")
    ap.add_argument("-s", "--simulate_display", action="store_true",
                    default=False, help="Don't send to e-paper (preview only)")
    ap.add_argument("-q", "--quantize_preview", action="store_true",
                    default=False, help="Save 7-color quantized preview")
    ap.add_argument("--width", type=int, default=800, help="Display width")
    ap.add_argument("--height", type=int, default=480, help="Display height")
    args = ap.parse_args()

    disp_w, disp_h = args.width, args.height

    # Swap axes for portrait orientation
    if args.portrait:
        disp_w, disp_h = disp_h, disp_w

    image = load_image(args.image)

    if args.resize_only:
        print(f"Resizing to {disp_w}x{disp_h}")
        image = cv2.resize(image, (disp_w, disp_h))
    else:
        image = crop(image, disp_w, disp_h, intelligent=not args.centre_crop)

    if not args.simulate_display:
        display_on_epaper(image)

    if args.output:
        save_image(args.output, image)
        print(f"Saved cropped image to {args.output}")

    if args.quantize_preview:
        preview = quantize_preview(image)
        preview_path = args.output if args.output else "preview_7color.png"
        if not args.output:
            preview.save(preview_path)
        else:
            base, ext = os.path.splitext(preview_path)
            preview.save(f"{base}_7color{ext}")
        print(f"Saved 7-color preview")


if __name__ == "__main__":
    main()

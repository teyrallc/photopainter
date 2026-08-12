#!/usr/bin/env python3
"""
Generate AI artwork using Stable Diffusion XL Turbo via OnnxStream.
Adapted from PaperPiAI (https://github.com/dylski/PaperPiAI)
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Both of these used to default to paths relative to the *working directory*,
# so the tool only ran if you happened to be standing in the project root —
# and failed with a bare "No such file" from the subprocess anywhere else.
DEFAULT_SD_BINARY = os.path.join(PROJECT_DIR, "OnnxStream", "src", "build", "sd")
DEFAULT_MODEL_DIR = os.path.join(
    PROJECT_DIR, "models", "stable-diffusion-xl-turbo-1.0-anyshape-onnxstream")


def choose_prompt(filename: str):
    """Load prompt parts from JSON and randomly combine them."""
    with open(filename) as file:
        prompts = json.load(file)
    return ' '.join(random.choice(part) for part in prompts)


def main():
    parser = argparse.ArgumentParser(description="Generate a new random picture using Stable Diffusion.")
    parser.add_argument("output_dir", help="Directory to save the output images")
    parser.add_argument("--prompts", default=None, help="The prompts JSON file to use")
    parser.add_argument("--prompt", default="", help="Custom prompt text (overrides --prompts)")
    parser.add_argument("--seed", type=int, default=None, help="The seed to use (random if not set)")
    parser.add_argument("--steps", type=int, default=5, help="The number of inference steps")
    parser.add_argument("--width", type=int, default=800, help="Image width")
    parser.add_argument("--height", type=int, default=480, help="Image height")
    parser.add_argument("--sd", default=DEFAULT_SD_BINARY,
                        help="Path to the stable diffusion binary")
    parser.add_argument("--model", default=DEFAULT_MODEL_DIR,
                        help="Path to the stable diffusion model")
    args = parser.parse_args()

    # Fail with something actionable rather than a bare OSError from Popen.
    if not os.path.isfile(args.sd) or not os.access(args.sd, os.X_OK):
        sys.exit(
            f"Stable Diffusion binary not found or not executable:\n"
            f"  {args.sd}\n\n"
            f"Build it with:  bash scripts/install-extras.sh --ai")
    if not os.path.isdir(args.model):
        sys.exit(
            f"Model weights not found:\n"
            f"  {args.model}\n\n"
            f"Download them with:  bash scripts/install-extras.sh --ai")

    # Default seed
    if args.seed is None:
        args.seed = random.randint(1, 10000)

    # Default prompts file
    if args.prompts is None:
        args.prompts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "prompts", "flowers.json")

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Select prompt
    prompt = args.prompt
    if not prompt:
        prompt = choose_prompt(args.prompts)

    # Create unique filename
    unique_arg = prompt.replace(' ', '_')[:64]
    unique_arg = f"{unique_arg}_seed_{args.seed}_steps_{args.steps}"
    fullpath = os.path.join(args.output_dir, f"{unique_arg}.png")

    # Construct the command
    cmd = [
        args.sd,
        "--xl", "--turbo",
        "--models-path", args.model,
        "--rpi-lowmem",
        "--prompt", prompt,
        "--seed", str(args.seed),
        "--output", fullpath,
        "--steps", str(args.steps),
        "--res", f"{args.width}x{args.height}"
    ]

    print(f"Prompt: {prompt}")
    print(f"Seed: {args.seed}")
    print(f"Output: {fullpath}")
    print(f"Command: {' '.join(cmd)}")
    print("Generating image... (this may take ~30 minutes on Pi Zero 2)")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Error: Generation failed with return code {result.returncode}")
        return

    print("Generation complete!")

    # Copy to shared output.png for easy access
    shared_fullpath = os.path.join(args.output_dir, "output.png")
    shutil.copyfile(fullpath, shared_fullpath)
    print(f"Copied to {shared_fullpath}")


if __name__ == "__main__":
    main()

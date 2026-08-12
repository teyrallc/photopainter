#!/usr/bin/env python3
"""Tests for the standalone CLI tools in src/.

These tools are not imported by the web service and their real dependencies
(OpenCV, libgpiod, the OnnxStream binary) are not installed by
scripts/install.sh. The heavy imports are stubbed so the parts that are plain
logic — panel palettes, GPIO chip discovery, path resolution, the guards that
produce an actionable message when a dependency is missing — are covered
anywhere, including in CI.

    python3 -m pytest tests/
    python3 tests/test_extras.py     # also runs standalone
"""

import os
import subprocess
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")


def _stub_vision_imports():
    """Let src/display_picture.py import without OpenCV or numpy present."""
    for name in ("cv2", "numpy"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)


def _load(module_name):
    _stub_vision_imports()
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    if REPO + "/web" not in sys.path:
        sys.path.insert(0, os.path.join(REPO, "web"))
    return __import__(module_name)


# ── display_picture ───────────────────────────────────────────────────────

def test_preview_palette_follows_the_fitted_panel():
    """The E panel has no orange; promising one in the preview is a lie."""
    dp = _load("display_picture")

    assert len(dp.PALETTE_6) // 3 == 6
    assert len(dp.PALETTE_7) // 3 == 7
    # Orange is what separates them, and it belongs only to the F panel.
    assert (255, 128, 0) == tuple(dp.PALETTE_7[-3:])
    assert 128 not in dp.PALETTE_6


def test_display_picture_no_longer_hardcodes_one_driver():
    """It must honour epd_model like the frame does."""
    source = open(os.path.join(SRC, "display_picture.py")).read()
    assert "from waveshare_epd import epd7in3e" not in source
    assert "epd_service.get_epd" in source


def test_configured_panel_reads_the_device_config():
    dp = _load("display_picture")
    # No config file on a dev box: must fall back, not raise.
    assert dp.configured_panel() in (None, "7in3e", "7in3f")


# ── display_buttons ───────────────────────────────────────────────────────

def test_gpio_chip_is_discovered_not_hardcoded():
    """gpiochip4 is a Pi 5; on a Pi Zero 2 W the SoC bank is gpiochip0."""
    db = _load("display_buttons")

    class FakeChip:
        LABELS = {
            "/dev/gpiochip0": ("raspberrypi-exp-gpio", 8),   # the expander
            "/dev/gpiochip1": ("pinctrl-bcm2835", 54),       # the real one
        }

        def __init__(self, path):
            self.path = path
            self.label, self._lines = self.LABELS.get(path, ("other", 4))

        def num_lines(self):
            return self._lines

        def close(self):
            pass

    fake = types.ModuleType("gpiod")
    fake.Chip = FakeChip

    real_exists = os.path.exists
    os.path.exists = lambda p: p in FakeChip.LABELS
    try:
        chip = db.find_gpio_chip(fake)
    finally:
        os.path.exists = real_exists

    # It must pick the SoC bank by label, not simply the lowest number.
    assert chip.path == "/dev/gpiochip1", chip.path
    assert "pinctrl" in chip.label


def test_no_gpio_chip_gives_an_actionable_error():
    db = _load("display_buttons")
    fake = types.ModuleType("gpiod")
    fake.Chip = lambda path: (_ for _ in ()).throw(OSError("nope"))

    real_exists = os.path.exists
    os.path.exists = lambda p: False
    try:
        db.find_gpio_chip(fake)
    except RuntimeError as exc:
        assert "gpio" in str(exc).lower()
    else:
        raise AssertionError("expected a RuntimeError naming the problem")
    finally:
        os.path.exists = real_exists


def test_buttons_reject_the_incompatible_gpiod_v2_api():
    """`pip install gpiod` gets v2, whose API this script cannot use."""
    db = _load("display_buttons")
    saved = sys.modules.get("gpiod")
    sys.modules["gpiod"] = types.ModuleType("gpiod")     # no v1 constants
    try:
        db.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected main() to exit on a v2 gpiod")
    finally:
        if saved is None:
            sys.modules.pop("gpiod", None)
        else:
            sys.modules["gpiod"] = saved


# ── generate_picture ──────────────────────────────────────────────────────

def test_generator_paths_are_project_relative():
    """The defaults used to resolve against the working directory."""
    gp = _load("generate_picture")
    assert gp.DEFAULT_SD_BINARY.startswith(REPO)
    assert gp.DEFAULT_MODEL_DIR.startswith(REPO)


def test_generator_explains_what_is_missing():
    """Run from elsewhere, it must still name the real paths and the fix."""
    with tempfile.TemporaryDirectory() as elsewhere:
        result = subprocess.run(
            [sys.executable, os.path.join(SRC, "generate_picture.py"), elsewhere],
            capture_output=True, text=True, cwd=elsewhere, timeout=60)

    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "install-extras.sh" in message, message
    assert REPO in message, message          # absolute, not cwd-relative


def test_prompt_files_match_what_the_generator_expects():
    """choose_prompt picks one entry per group and joins them."""
    gp = _load("generate_picture")
    for name in ("flowers.json", "landscapes.json"):
        path = os.path.join(REPO, "prompts", name)
        prompt = gp.choose_prompt(path)
        assert prompt and " " in prompt, f"{name} produced {prompt!r}"


# ── Packaging ─────────────────────────────────────────────────────────────

def test_extras_are_declared_but_not_in_the_base_install():
    """The frame must not drag OpenCV in; the extras file must declare it."""
    base = open(os.path.join(REPO, "requirements.txt")).read()
    extras = open(os.path.join(REPO, "requirements-extras.txt")).read()

    # Comments in requirements.txt do mention these packages, so check the
    # actual requirement lines rather than the file as a whole.
    for line in base.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert "opencv" not in stripped.lower(), stripped
            assert not stripped.lower().startswith("numpy"), stripped

    assert "opencv-contrib-python-headless" in extras
    assert "numpy" in extras


def test_install_extras_script_is_wired_up():
    script = os.path.join(REPO, "scripts", "install-extras.sh")
    assert os.access(script, os.X_OK), "install-extras.sh is not executable"

    # Every advertised mode has to be a real branch.
    body = open(script).read()
    for flag in ("--vision", "--buttons", "--ai", "--all"):
        assert flag in body, flag

    # And the base installer has to point at it, or nobody will find it.
    assert "install-extras.sh" in open(os.path.join(REPO, "scripts", "install.sh")).read()

    help_text = subprocess.run(["bash", script, "--help"],
                               capture_output=True, text=True, timeout=30)
    assert help_text.returncode == 0
    assert "set -euo" not in help_text.stdout, "help is leaking script body"


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}\n        {exc}")
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            failed += 1
            print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

"""Panel driver selection.

`lib/waveshare_epd/` carries drivers for two panels — the 7.3" E and the 7.3"
F — and every call site imported the E one by name. The F driver was therefore
dead code sitting next to code that looked identical, which is the arrangement
most likely to get one edited when the other was meant.

There is now one place that decides, driven by a config value, so the F panel
is either genuinely supported or genuinely absent. Nothing imports a driver
module directly any more.
"""

import logging

logger = logging.getLogger("vignette.epd")

# config value -> module name in lib/waveshare_epd/
SUPPORTED_MODELS = {
    "7in3e": "epd7in3e",
    "7in3f": "epd7in3f",
}

DEFAULT_MODEL = "7in3e"


def normalize_model(model):
    """Coerce a config value to a supported model, warning if it was wrong."""
    if model in SUPPORTED_MODELS:
        return model
    if model:
        logger.warning(f"Unknown panel model {model!r}; falling back to {DEFAULT_MODEL}")
    return DEFAULT_MODEL


def get_epd(model=None):
    """Return a ready-to-use EPD instance for the configured panel.

    Imported lazily and never cached: the driver holds SPI and GPIO handles,
    and every caller here already wraps its use in init/display/sleep.
    """
    model = normalize_model(model)
    module_name = SUPPORTED_MODELS[model]

    from importlib import import_module
    driver = import_module(f"waveshare_epd.{module_name}")
    return driver.EPD()

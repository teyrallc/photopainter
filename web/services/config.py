"""
Vignette configuration management.
Stores settings in a JSON file.
"""

import json
import logging
import os

logger = logging.getLogger("vignette.config")

DEFAULT_CONFIG = {
    "setup_complete": False,

    # Current display state
    "current_page": "photo",  # home, widget, photo
    "widget_mode": "weather",  # weather, calendar

    # Weather settings
    "weather_api_key": "",
    "weather_city": "",
    "weather_units": "metric",  # metric, imperial
    "weather_lang": "zh_tw",

    # Calendar settings
    "calendar_ical_url": "",

    # Photo settings
    "photo_rotation": 0,  # 0, 90, 180, 270
    "photo_fit_mode": "fit",  # fit (letterbox) or stretch

    # Display settings
    "auto_refresh_minutes": 30,
}


class Config:
    def __init__(self, config_path):
        self.config_path = config_path
        self._data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    saved = json.load(f)
                self._data.update(saved)
                logger.info(f"Config loaded from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            logger.info("Config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def update(self, data):
        self._data.update(data)
        self.save()

    def reset(self):
        self._data = dict(DEFAULT_CONFIG)
        self.save()
        logger.info("Config reset to defaults")

    def to_dict(self):
        return dict(self._data)

    @property
    def is_setup_complete(self):
        return self._data.get("setup_complete", False)

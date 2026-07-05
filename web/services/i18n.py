"""
Internationalization for Vignette web interface.
Supports English (en) and Traditional Chinese (zh).
"""

TRANSLATIONS = {
    "en": {
        "app_name": "Vignette",
        "dashboard": "Dashboard",
        "upload": "Upload",
        "gallery": "Gallery",
        "settings": "Settings",
        "manual": "Manual",
        "wifi_setup": "WiFi Setup",
        "logout": "Logout",

        # Dashboard
        "display_page": "Display Page",
        "photo_control": "Photo Control",
        "display_status": "Display Status",
        "quick_actions": "Quick Actions",
        "system_info": "System Info",
        "remote_mgmt": "Remote Management",
        "recent_photos": "Recent Photos",
        "current": "Current",
        "prev": "Prev",
        "next": "Next",
        "latest": "Latest",
        "test_pattern": "Test Pattern",
        "refresh": "Refresh",
        "delete": "Delete",
        "update_display": "Update Display",
        "clear": "Clear",
        "sleep": "Sleep",
        "no_photo": "No photo displayed",
        "no_photos_yet": "No photos yet. Upload one!",

        # Pages
        "home": "Home",
        "widget": "Widget",
        "photo": "Photo",
        "split": "Split",
        "weather": "Weather",
        "calendar": "Calendar",
        "switch_to": "Switch to",

        # Upload
        "select_image": "Select Image",
        "drag_or_click": "Drag image here or click to select",
        "rotation": "Rotation",
        "fit_mode": "Fit Mode",
        "fit_letterbox": "Fit (keep ratio)",
        "stretch_fill": "Stretch (fill)",
        "uploading": "Uploading...",
        "preview": "Preview",
        "original": "Original",
        "epaper_preview": "E-paper preview (6 colors)",
        "display_on_epaper": "Display on E-paper",
        "upload_preview_hint": "Upload an image to see preview",

        # Settings
        "weather_settings": "Weather",
        "calendar_settings": "Calendar",
        "photo_settings": "Photo Display",
        "system_settings": "System",
        "api_key": "API Key",
        "city": "City",
        "units": "Units",
        "celsius": "Celsius",
        "fahrenheit": "Fahrenheit",
        "ical_url": "iCal URL",
        "ical_hint": "Google Calendar: Settings > Calendar > Secret address in iCal format",
        "default_rotation": "Default Rotation",
        "save": "Save",
        "test": "Test",
        "show_qr": "Show QR Code on Display",
        "factory_reset": "Factory Reset",
        "factory_reset_warn": "Factory Reset will clear all settings and show the WiFi setup screen.",
        "normal": "Normal",

        # WiFi
        "wifi_config": "WiFi Configuration",
        "current_connection": "Current Connection",
        "available_networks": "Available Networks",
        "scan_networks": "Scan",
        "connect": "Connect",
        "password": "Password",
        "connected": "Connected",
        "not_connected": "Not connected",
        "connecting": "Connecting...",
        "ssid": "Network Name",
        "signal": "Signal",

        # System
        "update_software": "Update Software",
        "reboot": "Reboot",
        "shutdown": "Shutdown",
        "status": "Status",
        "showing": "Showing",
        "last_update": "Last Update",
        "total_images": "Total Images",
        "loading": "Loading...",
        "host": "Host",
        "ip": "IP",
        "temp": "Temp",
        "ram": "RAM",
        "disk": "Disk",
        "uptime": "Uptime",

        # Gallery
        "gallery_empty": "Gallery is empty",
        "image_preview": "Image Preview",
        "display_to_epaper": "Display on E-paper",
        "close": "Close",
        "confirm_delete": "Are you sure you want to delete",
        "deleted": "Deleted",
        "display": "Display",
        "images_count": "images",
        "batch_upload": "Batch Upload",
        "upload_complete": "Upload complete",
        "select_all": "Select All",
        "deselect_all": "Deselect All",
        "selected": "selected",
        "load_more": "Load More",

        # Google Drive
        "gdrive_connect": "Connect Google Drive",
        "gdrive_disconnect": "Disconnect",
        "gdrive_select": "Select Photos",
        "gdrive_download": "Download Selected",
        "gdrive_downloading": "Downloading",
        "gdrive_download_ok": "Downloaded",
        "gdrive_no_images": "No images found in Google Drive",
        "gdrive_auth_expired": "Google Drive session expired, re-authenticating...",
        "gdrive_confirm_disconnect": "Disconnect Google Drive?",
        "gdrive_setup_hint": "Setup Google Drive",
        "gdrive_setup_instructions": "Create a project at console.cloud.google.com, enable Drive API, create OAuth 2.0 credentials.",
        "gdrive_steps_title": "Setup Steps",
        "gdrive_step1": "Go to console.cloud.google.com > Create Project",
        "gdrive_step2": "APIs & Services > Enable Google Drive API",
        "gdrive_step3": "Credentials > Create OAuth Client ID (Web type)",
        "gdrive_step4": "Add JS origin: http://YOUR_PI_IP:5000",

        # Setup
        "first_time_setup": "First Time Setup",
        "weather_hint": "Get free key at openweathermap.org",
        "city_placeholder": "e.g., Taipei, Tokyo, New York",
        "ical_placeholder": "https://calendar.google.com/calendar/ical/...",
        "default_page": "Default Page",
        "complete_setup": "Complete Setup",
        "settings_changeable": "All settings can be changed later in Settings page",

        # Manual sections
        "hardware_req": "Hardware Requirements",
        "assembly_guide": "Assembly",
        "installation": "Installation",
        "web_console": "Web Console",
        "photo_navigation": "Photo Navigation",
        "api_reference": "API Reference",
        "service_mgmt": "Service Management",
        "troubleshooting": "Troubleshooting",

        # Common
        "confirm_reboot": "Reboot the Raspberry Pi?",
        "confirm_shutdown": "Shutdown the Pi? Need manual power cycle to restart.",
        "confirm_update": "Update software?",
        "confirm_reset": "Are you sure? This will clear ALL settings.",
        "display_busy": "Display is busy",

        # Slideshow
        "slideshow": "Slideshow",
        "slideshow_interval": "Interval",
        "slideshow_order": "Order",
        "slideshow_photos": "Photos",
        "slideshow_running": "Slideshow is running",
        "slideshow_empty_hint": "Select none = use all photos",
        "sequential": "Sequential",
        "random": "Random",
        "select_photos": "Select Photos",
        "all_photos": "All",
        "start": "Start",
        "stop": "Stop",
        "minute": "min",
        "minutes": "min",
        "hour": "hour",
        "updating": "Updating",

        # JS app.js strings
        "sending_to_epaper": "Sending to e-paper...",
        "displayed_ok": "Image displayed!",
        "confirm_clear": "Clear the display?",
        "clearing": "Clearing...",
        "sleep_ok": "Display sleeping",
        "loading_next": "Loading next...",
        "loading_prev": "Loading previous...",
        "loading_latest": "Loading latest...",
        "confirm_test": "Send test pattern to e-paper?",
        "sending_test": "Sending test pattern...",
        "slideshow_started": "Slideshow started",
        "slideshow_stopped": "Slideshow stopped",
        "rebooting": "Rebooting...",
        "shutting_down": "Shutting down...",
        "action_ok": "OK",
        "action_failed": "Failed",
        "load_failed": "Failed to load",
        "update_started": "Update started; the device will restart shortly.",
        "upload_options_hint": "Rotation & fit apply to the next file you choose.",
    },
    "zh": {
        "app_name": "Vignette",
        "dashboard": "\u63a7\u5236\u53f0",
        "upload": "\u4e0a\u50b3\u5716\u7247",
        "gallery": "\u5716\u5eab",
        "settings": "\u8a2d\u5b9a",
        "manual": "\u624b\u518a",
        "wifi_setup": "WiFi \u8a2d\u5b9a",
        "logout": "\u767b\u51fa",

        # Dashboard
        "display_page": "\u986f\u793a\u9801\u9762",
        "photo_control": "\u7167\u7247\u63a7\u5236",
        "display_status": "\u986f\u793a\u5668\u72c0\u614b",
        "quick_actions": "\u5feb\u901f\u64cd\u4f5c",
        "system_info": "\u7cfb\u7d71\u8cc7\u8a0a",
        "remote_mgmt": "\u9060\u7aef\u7ba1\u7406",
        "recent_photos": "\u6700\u8fd1\u7167\u7247",
        "current": "\u76ee\u524d",
        "prev": "\u4e0a\u4e00\u5f35",
        "next": "\u4e0b\u4e00\u5f35",
        "latest": "\u6700\u65b0",
        "test_pattern": "\u6e2c\u8a66\u5716\u6848",
        "refresh": "\u91cd\u65b0\u6574\u7406",
        "delete": "\u522a\u9664",
        "update_display": "\u66f4\u65b0\u5230\u87a2\u5e55",
        "clear": "\u6e05\u9664\u87a2\u5e55",
        "sleep": "\u4f11\u7720",
        "no_photo": "\u5c1a\u672a\u986f\u793a\u7167\u7247",
        "no_photos_yet": "\u5c1a\u7121\u7167\u7247\uff0c\u8acb\u4e0a\u50b3\u3002",

        # Pages
        "home": "\u9996\u9801",
        "widget": "\u5c0f\u5de5\u5177",
        "photo": "\u7167\u7247",
        "split": "\u5206\u5272",
        "weather": "\u5929\u6c23",
        "calendar": "\u884c\u4e8b\u66c6",
        "switch_to": "\u5207\u63db\u5230",

        # Upload
        "select_image": "\u9078\u64c7\u5716\u7247",
        "drag_or_click": "\u62d6\u66f3\u5716\u7247\u5230\u9019\u88e1\uff0c\u6216\u9ede\u64ca\u9078\u64c7",
        "rotation": "\u65cb\u8f49",
        "fit_mode": "\u586b\u5145\u6a21\u5f0f",
        "fit_letterbox": "\u7b49\u6bd4\u7559\u767d",
        "stretch_fill": "\u62c9\u4f38\u586b\u6eff",
        "uploading": "\u4e0a\u50b3\u4e2d...",
        "preview": "\u9810\u89bd",
        "original": "\u539f\u59cb",
        "epaper_preview": "\u96fb\u5b50\u7d19\u6548\u679c (6\u8272)",
        "display_on_epaper": "\u986f\u793a\u5230\u96fb\u5b50\u7d19",
        "upload_preview_hint": "\u4e0a\u50b3\u5716\u7247\u5f8c\u986f\u793a\u9810\u89bd",

        # Settings
        "weather_settings": "\u5929\u6c23",
        "calendar_settings": "\u884c\u4e8b\u66c6",
        "photo_settings": "\u7167\u7247\u986f\u793a",
        "system_settings": "\u7cfb\u7d71",
        "api_key": "API Key",
        "city": "\u57ce\u5e02",
        "units": "\u55ae\u4f4d",
        "celsius": "\u651d\u6c0f (°C)",
        "fahrenheit": "\u83ef\u6c0f (°F)",
        "ical_url": "iCal URL",
        "ical_hint": "Google Calendar: \u8a2d\u5b9a > \u884c\u4e8b\u66c6 > iCal \u683c\u5f0f\u7684\u79d8\u5bc6\u5730\u5740",
        "default_rotation": "\u9810\u8a2d\u65cb\u8f49",
        "save": "\u5132\u5b58",
        "test": "\u6e2c\u8a66",
        "show_qr": "\u5728\u87a2\u5e55\u986f\u793a QR Code",
        "factory_reset": "\u5168\u90e8\u91cd\u8a2d",
        "factory_reset_warn": "\u5168\u90e8\u91cd\u8a2d\u6703\u6e05\u9664\u6240\u6709\u8a2d\u5b9a\u4e26\u986f\u793a WiFi \u8a2d\u5b9a\u756b\u9762\u3002",
        "normal": "\u6b63\u5e38",

        # WiFi
        "wifi_config": "WiFi \u8a2d\u5b9a",
        "current_connection": "\u76ee\u524d\u9023\u7dda",
        "available_networks": "\u53ef\u7528\u7db2\u8def",
        "scan_networks": "\u6383\u63cf",
        "connect": "\u9023\u7dda",
        "password": "\u5bc6\u78bc",
        "connected": "\u5df2\u9023\u7dda",
        "not_connected": "\u672a\u9023\u7dda",
        "connecting": "\u9023\u7dda\u4e2d...",
        "ssid": "\u7db2\u8def\u540d\u7a31",
        "signal": "\u8a0a\u865f",

        # System
        "update_software": "\u66f4\u65b0\u7a0b\u5f0f",
        "reboot": "\u91cd\u65b0\u555f\u52d5",
        "shutdown": "\u95dc\u6a5f",
        "status": "\u72c0\u614b",
        "showing": "\u986f\u793a",
        "last_update": "\u6700\u5f8c\u66f4\u65b0",
        "total_images": "\u5716\u7247\u7e3d\u6578",
        "loading": "\u8f09\u5165\u4e2d...",
        "host": "\u4e3b\u6a5f",
        "ip": "IP \u4f4d\u5740",
        "temp": "\u6eab\u5ea6",
        "ram": "\u8a18\u61b6\u9ad4",
        "disk": "\u78c1\u789f",
        "uptime": "\u904b\u884c\u6642\u9593",

        # Gallery
        "gallery_empty": "\u5716\u5eab\u662f\u7a7a\u7684",
        "image_preview": "\u5716\u7247\u9810\u89bd",
        "display_to_epaper": "\u986f\u793a\u5230\u96fb\u5b50\u7d19",
        "close": "\u95dc\u9589",
        "confirm_delete": "\u78ba\u5b9a\u8981\u522a\u9664",
        "deleted": "\u5df2\u522a\u9664",
        "display": "\u986f\u793a",
        "images_count": "\u5f35",
        "batch_upload": "\u6279\u91cf\u4e0a\u50b3",
        "upload_complete": "\u4e0a\u50b3\u5b8c\u6210",
        "select_all": "\u5168\u9078",
        "deselect_all": "\u53d6\u6d88\u5168\u9078",
        "selected": "\u5df2\u9078",
        "load_more": "\u8f09\u5165\u66f4\u591a",

        # Google Drive
        "gdrive_connect": "\u9023\u7d50 Google Drive",
        "gdrive_disconnect": "\u65b7\u958b\u9023\u7d50",
        "gdrive_select": "\u9078\u64c7\u7167\u7247",
        "gdrive_download": "\u4e0b\u8f09\u5df2\u9078",
        "gdrive_downloading": "\u4e0b\u8f09\u4e2d",
        "gdrive_download_ok": "\u5df2\u4e0b\u8f09",
        "gdrive_no_images": "Google Drive \u4e2d\u6c92\u6709\u5716\u7247",
        "gdrive_auth_expired": "Google Drive \u5df2\u904e\u671f\uff0c\u91cd\u65b0\u9a57\u8b49\u4e2d...",
        "gdrive_confirm_disconnect": "\u78ba\u5b9a\u8981\u65b7\u958b Google Drive \u55ce\uff1f",
        "gdrive_setup_hint": "\u8a2d\u5b9a Google Drive",
        "gdrive_setup_instructions": "\u5728 console.cloud.google.com \u5efa\u7acb\u5c08\u6848\uff0c\u555f\u7528 Drive API\u3002",
        "gdrive_steps_title": "\u8a2d\u5b9a\u6b65\u9a5f",
        "gdrive_step1": "\u524d\u5f80 console.cloud.google.com > \u5efa\u7acb\u5c08\u6848",
        "gdrive_step2": "API \u8207\u670d\u52d9 > \u555f\u7528 Google Drive API",
        "gdrive_step3": "\u6191\u8b49 > \u5efa\u7acb OAuth \u7528\u6236\u7aef ID (\u7db2\u9801\u61c9\u7528\u7a0b\u5f0f)",
        "gdrive_step4": "\u65b0\u589e JS \u4f86\u6e90: http://YOUR_PI_IP:5000",

        # Setup
        "first_time_setup": "\u521d\u6b21\u8a2d\u5b9a",
        "weather_hint": "\u5728 openweathermap.org \u53d6\u5f97\u514d\u8cbb API Key",
        "city_placeholder": "\u4f8b\u5982\uff1aTaipei, Tokyo, New York",
        "ical_placeholder": "https://calendar.google.com/calendar/ical/...",
        "default_page": "\u9810\u8a2d\u9801\u9762",
        "complete_setup": "\u5b8c\u6210\u8a2d\u5b9a",
        "settings_changeable": "\u6240\u6709\u8a2d\u5b9a\u53ef\u4ee5\u7a0d\u5f8c\u5728\u8a2d\u5b9a\u9801\u9762\u4fee\u6539",

        # Manual sections
        "hardware_req": "\u786c\u9ad4\u9700\u6c42",
        "assembly_guide": "\u7d44\u88dd\u8aaa\u660e",
        "installation": "\u8edf\u9ad4\u5b89\u88dd",
        "web_console": "Web \u63a7\u5236\u53f0",
        "photo_navigation": "\u7167\u7247\u5c0e\u822a",
        "api_reference": "API \u6587\u4ef6",
        "service_mgmt": "\u670d\u52d9\u7ba1\u7406",
        "troubleshooting": "\u6545\u969c\u6392\u9664",

        # Common
        "confirm_reboot": "\u78ba\u5b9a\u8981\u91cd\u65b0\u555f\u52d5 Raspberry Pi \u55ce\uff1f",
        "confirm_shutdown": "\u78ba\u5b9a\u8981\u95dc\u6a5f\u55ce\uff1f\u95dc\u6a5f\u5f8c\u9700\u8981\u624b\u52d5\u91cd\u65b0\u63a5\u96fb\u3002",
        "confirm_update": "\u78ba\u5b9a\u8981\u66f4\u65b0\u7a0b\u5f0f\u55ce\uff1f",
        "confirm_reset": "\u78ba\u5b9a\u8981\u5168\u90e8\u91cd\u8a2d\u55ce\uff1f\u6240\u6709\u8a2d\u5b9a\u5c07\u88ab\u6e05\u9664\u3002",
        "display_busy": "\u986f\u793a\u5668\u5fd9\u788c\u4e2d",

        # Slideshow
        "slideshow": "\u8f2a\u64ad",
        "slideshow_interval": "\u66f4\u65b0\u9031\u671f",
        "slideshow_order": "\u64ad\u653e\u9806\u5e8f",
        "slideshow_photos": "\u7167\u7247",
        "slideshow_running": "\u8f2a\u64ad\u4e2d",
        "slideshow_empty_hint": "\u4e0d\u9078 = \u5168\u90e8\u7167\u7247",
        "sequential": "\u9806\u5e8f",
        "random": "\u96a8\u6a5f",
        "select_photos": "\u9078\u64c7\u7167\u7247",
        "all_photos": "\u5168\u90e8",
        "start": "\u958b\u59cb",
        "stop": "\u505c\u6b62",
        "minute": "\u5206\u9418",
        "minutes": "\u5206\u9418",
        "hour": "\u5c0f\u6642",
        "updating": "\u66f4\u65b0\u4e2d",

        # JS app.js strings
        "sending_to_epaper": "\u6b63\u5728\u767c\u9001\u5230\u96fb\u5b50\u7d19...",
        "displayed_ok": "\u5716\u7247\u5df2\u986f\u793a\uff01",
        "confirm_clear": "\u78ba\u5b9a\u8981\u6e05\u9664\u87a2\u5e55\u55ce\uff1f",
        "clearing": "\u6b63\u5728\u6e05\u9664...",
        "sleep_ok": "\u87a2\u5e55\u5df2\u4f11\u7720",
        "loading_next": "\u8f09\u5165\u4e0b\u4e00\u5f35...",
        "loading_prev": "\u8f09\u5165\u4e0a\u4e00\u5f35...",
        "loading_latest": "\u8f09\u5165\u6700\u65b0\u7167\u7247...",
        "confirm_test": "\u78ba\u5b9a\u8981\u767c\u9001\u6e2c\u8a66\u5716\u6848\u55ce\uff1f",
        "sending_test": "\u6b63\u5728\u767c\u9001\u6e2c\u8a66\u5716\u6848...",
        "slideshow_started": "\u8f2a\u64ad\u5df2\u958b\u59cb",
        "slideshow_stopped": "\u8f2a\u64ad\u5df2\u505c\u6b62",
        "rebooting": "\u91cd\u65b0\u555f\u52d5\u4e2d...",
        "shutting_down": "\u95dc\u6a5f\u4e2d...",
        "action_ok": "\u5b8c\u6210",
        "action_failed": "\u5931\u6557",
        "load_failed": "\u8f09\u5165\u5931\u6557",
        "update_started": "\u5df2\u958b\u59cb\u66f4\u65b0\uff0c\u88dd\u7f6e\u5c07\u91cd\u65b0\u555f\u52d5\u3002",
        "upload_options_hint": "\u65cb\u8f49\u8207\u7e2e\u653e\u8a2d\u5b9a\u6703\u5957\u7528\u5230\u63a5\u4e0b\u4f86\u9078\u64c7\u7684\u6a94\u6848\u3002",
    },
}


class _Translations:
    """Wrapper so templates can safely use ``{{ t.key }}`` for ANY key.

    A plain dict is unsafe here: Jinja resolves ``t.clear`` with getattr BEFORE
    item lookup, so for a dict it returns the bound ``dict.clear`` method (which
    renders as ``<built-in method clear ...>``) for every name that collides
    with a dict method (clear, get, items, keys, values, update, copy, pop, …).
    This object exposes no such methods; attribute/item access returns the
    translation string, or '' when the key is absent (per-key English fallback
    is already merged in below)."""
    __slots__ = ("_d",)

    def __init__(self, data):
        self._d = data

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self._d.get(name, "")

    def __getitem__(self, name):
        return self._d.get(name, "")

    def __contains__(self, name):
        return name in self._d


def get_translations(lang="en"):
    """Get translations for a language, with per-key fallback to English.

    Merging over a copy of the English dict means a key that exists only in `en`
    renders its English text in every language. Returns a _Translations wrapper
    so ``{{ t.key }}`` never resolves to a dict method (see class docstring)."""
    base = dict(TRANSLATIONS["en"])
    if lang != "en" and lang in TRANSLATIONS:
        base.update(TRANSLATIONS[lang])
    return _Translations(base)

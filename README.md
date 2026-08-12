# Vignette - H System Smart Display

A smart display system based on the Waveshare 7.3" 6-color e-paper display, running on Raspberry Pi Zero 2 W.

## Architecture

```
┌──────────────────────────────────┐
│  Phone / Tablet / Computer        │
│  Browser → http://<Pi-IP>:5000   │
└──────────────┬───────────────────┘
               │ WiFi
┌──────────────▼───────────────────┐
│  Raspberry Pi Zero 2 W            │
│  ┌─────────────────────────────┐  │
│  │  Flask Web Server (port 5000)│  │
│  │  - Upload / manage photos    │  │
│  │  - Photo navigation          │  │
│  │  - Remote management         │  │
│  │  - System monitoring         │  │
│  └──────────┬──────────────────┘  │
│             │ SPI                   │
│  ┌──────────▼──────────────────┐  │
│  │  7.3" 6-color e-paper        │  │
│  │  800x480, 7 colors           │  │
│  └─────────────────────────────┘  │
└────────────────────────────────────┘
```

## Features

- **Web Remote Control** - Operate from any device via browser (responsive UI)
- **Photo Upload** - Drag-and-drop upload with real-time 7-color e-paper preview
- **Photo Navigation** - Browse photos via web virtual buttons (prev / next / latest)
- **Test Pattern** - Send a 7-color test pattern with one click to verify hardware
- **Remote Management** - Update code, reboot, or shut down remotely
- **System Monitoring** - CPU temperature, memory, disk usage, and uptime

## Hardware Requirements

| Item | Spec |
|------|------|
| Board | Raspberry Pi Zero 2 W |
| Carrier | Waveshare RPi Zero PhotoPainter Board |
| Display | Waveshare 7.3" 6-color e-paper (800 x 480) |
| Battery | 3.7V 1500mAh Li-Po |
| Storage | 32GB+ microSD (64GB recommended) |

## Quick Start

### 1. Prepare Raspberry Pi

```bash
# Enable SPI
sudo raspi-config
# → Interface Options → SPI → Enable
```

### 2. Install

```bash
git clone <your-repository-url> Vignette
cd Vignette
bash scripts/install.sh
```

### 3. Access Web UI

```
http://<Pi-IP>:5000
```

## Service Management

```bash
sudo systemctl status vignette     # Check status
sudo systemctl restart vignette    # Restart service
sudo systemctl stop vignette       # Stop service
journalctl -u vignette -f          # View live logs
```

## Updating the Code

To pull the latest changes from GitHub and restart the service:

```bash
cd ~/Vignette
git pull origin main
sudo systemctl restart vignette
```

Or use the built-in update script:

```bash
bash scripts/update.sh
```

You can also trigger a remote update from the Web UI under **Settings → System → Update**.

## Tests

The e-paper panel and NetworkManager are stubbed, so the suite runs on any
machine — no Pi and no display needed:

```bash
python3 -m pytest tests/      # or: python3 tests/test_smoke.py
```

It asks every route for a response and checks the security boundaries
(secret redaction, the config write allowlist, same-origin enforcement,
path containment on filenames). Run it before pushing.

## API Reference

All endpoints require a signed-in session unless noted. `/api/wifi/*` is the
one exception, and only while the device is unpaired — see
**Authentication** below.

### Photos

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload one image |
| POST | `/api/upload/batch` | Upload several images |
| GET | `/api/images` | List images |
| DELETE | `/api/images/<filename>` | Delete image |
| GET | `/api/preview/<filename>` | 7-color preview of a file |
| GET | `/api/preview/current` | Preview of the current page as rendered |
| POST | `/api/display` | Display image on e-paper |
| POST | `/api/photo/next` · `/prev` · `/latest` | Move through the library |
| GET | `/api/photo/current` | Current photo and index |
| POST | `/api/photo/goto/<idx>` | Jump to an index |
| POST | `/api/photo/rotation` | Set rotation (0/90/180/270) |
| POST | `/api/photo/fit_mode` | Set `fit` or `stretch` |

### Pages and widgets

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/page/switch` | Switch home / widget / photo |
| POST | `/api/page/refresh` | Re-render and push the current page |
| POST | `/api/page/qr` | Show the pairing QR page |
| POST | `/api/widget/set` · `/api/widget/toggle` | Weather / calendar / split |
| GET | `/api/weather` · `/api/calendar` | Fetch the underlying data |

### Slideshow

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/slideshow/start` · `/stop` | Control the slideshow |
| GET | `/api/slideshow/status` | Current slideshow settings |

### Google Drive

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/gdrive/config` | Client ID and connection state |
| POST | `/api/gdrive/auth` | Exchange an auth code for tokens |
| GET | `/api/gdrive/callback` | OAuth redirect target |
| POST | `/api/gdrive/disconnect` | Forget the stored tokens |
| GET | `/api/gdrive/files` | List images in Drive |
| POST | `/api/gdrive/download` | Import selected files |

### Display and system

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/display/test` | 7-color test pattern |
| POST | `/api/clear` · `/api/sleep` | Clear / sleep the panel |
| GET | `/api/status` · `/api/system/info` | Device state |
| POST | `/api/system/update` | Pull and restart |
| POST | `/api/system/reboot` · `/api/system/shutdown` | Power control |
| POST | `/api/reset` | Factory reset. `{"delete_photos": true}` also wipes the library |
| GET | `/api/config` | Settings, with every secret redacted |
| POST | `/api/config` | Change settings. Credentials and WiFi are not writable here |
| POST | `/api/lang` | Switch `en` / `zh` |

### WiFi and setup

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/wifi/status` · `/api/wifi/scan` | Network state and nearby SSIDs |
| POST | `/api/wifi/connect` | Join a network (runs in the background) |
| GET | `/api/wifi/connect/status` | Poll the join result — open to all, by design |
| POST | `/api/setup` | Save first-run weather / calendar settings |

## Authentication

One admin account per device, registered on first use and confirmed by a
six-digit code shown **on the e-paper panel** — so registering and resetting
the password both require physical access to the device.

- While unpaired, only the pairing portal (`/`, `/setup`, `/api/wifi/*`) answers
  without a session. Everything else already requires sign-in.
- Once paired, `/api/wifi/connect/status` is the only endpoint that stays open;
  the phone polls it on the device's new address before anyone can sign in.
- State-changing requests are rejected when they arrive from another origin.
- Secrets — the session key, password hash, WiFi password, API keys, OAuth
  tokens — are never returned by an API or rendered into a page. The settings
  form shows a placeholder instead and treats a blank field as "keep the
  stored value".

## Project Structure

```
Vignette/
├── lib/waveshare_epd/           # Waveshare e-paper driver
│   ├── epd7in3e.py              # 7.3" 6-color display driver (E model)
│   └── epdconfig.py             # SPI/GPIO hardware config
├── src/
│   ├── display_picture.py       # Image processing & display (CLI)
│   └── display_buttons.py       # GPIO button controller
├── web/
│   ├── app.py                   # Flask web application
│   ├── auth.py                  # Authentication logic
│   ├── services/                # Business logic services
│   ├── templates/               # HTML templates
│   └── static/                  # CSS/JS static files
├── scripts/
│   ├── install.sh               # Installation script
│   ├── update.sh                # Remote update script
│   └── vignette.service         # systemd service file
├── tests/
│   └── test_smoke.py            # Runs off-device; hardware is stubbed
├── output/                      # Image output directory
├── requirements.txt             # Python dependencies
├── LICENSE
└── README.md
```

> `src/` holds standalone CLI tools (saliency-aware display, Stable Diffusion
> generation) that the web service never imports. They need extra dependencies
> — `opencv-contrib-python`, `numpy`, `gpiod` — and, for generation, an
> OnnxStream build plus model weights. None of that is installed by
> `scripts/install.sh`.

## Credits

- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - E-paper driver
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - Hardware reference

## License

MIT License

<img src="web/static/img/vignette-logo.svg" width="320" alt="Vignette">

&copy; 2026 Vignette

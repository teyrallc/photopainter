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
git clone https://github.com/teyrallc/Vignette.git
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

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload image |
| POST | `/api/display` | Display image on e-paper |
| GET | `/api/preview/<filename>` | 7-color preview |
| GET | `/api/images` | List images |
| DELETE | `/api/images/<filename>` | Delete image |
| POST | `/api/photo/next` | Next photo |
| POST | `/api/photo/prev` | Previous photo |
| POST | `/api/photo/latest` | Latest photo |
| POST | `/api/display/test` | Test pattern |
| POST | `/api/clear` | Clear display |
| POST | `/api/sleep` | Display sleep |
| GET | `/api/system/info` | System info |
| POST | `/api/system/update` | Remote update |
| POST | `/api/system/reboot` | Reboot |
| POST | `/api/system/shutdown` | Shutdown |

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
├── output/                      # Image output directory
├── requirements.txt             # Python dependencies
└── README.md
```

## Credits

- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - E-paper driver
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - Hardware reference

## License

MIT License

<img src="web/static/img/vignette-logo.svg" width="320" alt="Vignette">

&copy; 2026 Teyra LLC

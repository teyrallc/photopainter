# Vignette

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
- **Photo Sources** - Import from Google Drive, or subscribe to an iCloud shared album and let the frame pull new photos on its own
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

The pairing screen on the e-paper panel shows the hotspot name and password
for this specific device, plus a QR code. Join it, follow the second QR, and
pick your WiFi network.

Once it is on your network, the panel shows the address to use. If remote
access is configured that address works from anywhere; otherwise it is the
local one:

```
http://<Pi-IP>:5000
```

## Photo sources

Photos reach the frame three ways: dropped onto **Upload**, imported from
**Google Drive**, or pulled from an **iCloud shared album**. However they get
there they end up as ordinary files in `output/`, so the slideshow, the photo
page and the gallery treat them all alike.

### iCloud shared album

A shared album with **Public Website** turned on is readable from its link
alone — there is no account to sign in to, which is the whole reason it suits
a device with no keyboard. In Photos: open the album → **People** (or
*Subscribers*) → turn on **Public Website** → copy the link. Paste it into
**Settings → iCloud Shared Album** and press *Connect album*.

- **Gallery → iCloud** opens a picker: choose photos, or import everything.
  Photos already on the frame are marked, so importing twice cannot make
  duplicates.
- With **Pull new photos automatically** on, the frame re-checks the album on
  a schedule (hourly by default, 15 minutes at the fastest) and downloads only
  what it does not already have. Add a photo from your phone and it appears on
  the frame by itself — and, if the slideshow is running, in the rotation.
- Videos are skipped, and the ledger of what has been imported lives in
  `icloud_album.json` beside `config.json`. Deleting a photo from the gallery
  lets the next sync bring it back; *Disconnect* forgets the album and leaves
  the photos.

Treat the link as a credential: anyone who has it can see the album.

## Weather

**Settings → Weather** takes an [OpenWeatherMap](https://openweathermap.org/api)
key and a place. The place can be a plain name (`Taipei`), a name with a
country code when the name is ambiguous (`Kaohsiung, TW`), or a
`latitude, longitude` pair (`25.03, 121.56`) for somewhere the gazetteer does
not know by the name you use.

*Test* always asks upstream rather than reading the cache, so it reports the
settings as they are now. If the place cannot be found, or the key is
rejected, it says so — it will not fall back to the last place that worked.

## Remote Access

By default the console only answers on the same network as the display, which
is not much use for a frame you gave to somebody. Two ways to reach it from
anywhere:

### Your own domain, via Cloudflare Tunnel (recommended)

A fixed address that never changes — `https://yilin.example.com`. No port
forwarding, no static IP, no certificate management; the tunnel dials out, so
nothing is exposed inbound. Requires the domain's DNS to be on Cloudflare (the
free plan is enough).

```bash
bash scripts/setup-tunnel.sh yilin.example.com
```

That installs `cloudflared`, walks you through the Cloudflare login, creates a
named tunnel and its DNS record, installs it as a systemd service so it starts
at boot, and points Vignette's own settings at the new address. Use a
**subdomain** — a subpath like `example.com/yilin` would need every front-end
path rewritten.

During the login step the browser asks which domain to authorize. **Pick the
one that owns your hostname** — `example.com` for `yilin.example.com`. Choosing
another domain on the same account does not fail: `cloudflared` then treats the
hostname as a name relative to *that* zone and creates
`yilin.example.com.the-other-domain.com`, which nothing resolves. The script
checks the record it actually created and stops if it does not match.

If the address does not load right after setup, the likely cause is a cached
"no such name" answer from before the record existed — Cloudflare's SOA keeps
those for 30 minutes. Check from a phone on mobile data, which uses a different
resolver, before assuming the tunnel is broken.

### ngrok (quick, but the address moves)

Free ngrok issues a **new hostname on every reconnect**, so any address you
wrote down goes stale and the panel has to be repainted each time. Fine for a
prototype, wrong for something on a wall.

1. Get a free authtoken from
   [dashboard.ngrok.com](https://dashboard.ngrok.com/get-started/your-authtoken).
2. **Settings → Remote Access** → method `ngrok`, paste it, save.

### Either way

The supervisor retries with backoff, notices a dead tunnel and reconnects, and
never lets a dead tunnel look like a working one. The local address keeps
working alongside the public one. **Settings → Remote Access → Connection
method → Off** disables it entirely.

Set `auto_refresh_interval` to `0` in the config to stop the panel refreshing
on a timer.

## When the network goes away

If the saved WiFi is unreachable for one minute, the display raises its own
pairing hotspot and shows the pairing screen again, so it can be pointed at a
new network without a keyboard or a reset.

**This is not a factory reset.** The WiFi password, the admin account and every
photo are kept. While the fallback hotspot is up the display quietly retries the
saved network every five minutes, so a router that was simply slow to reboot
recovers on its own with nobody touching anything.

### Turning that off on a finished unit

Raising the hotspot puts `wlan0` into AP mode, which severs the link it is
reporting on. That is the right trade while a device is still being set up and
wrong for one hanging on a wall, where a two-minute router reboot should not
turn into a frame advertising a pairing network. Set `setup_hotspot_fallback`
to `false` and the device keeps retrying the saved network quietly and forever
instead, never taking itself off the air:

```bash
POST /api/config   {"setup_hotspot_fallback": false}
```

A device that has never been paired still raises the hotspot either way — it
has no saved network to retry and no other way in.

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

## Optional extras

The three tools in `src/` are independent of the frame — nothing in `web/`
imports them — and they need dependencies `scripts/install.sh` deliberately
does not install. `scripts/install-extras.sh` fills those gaps, one part at a
time:

```bash
bash scripts/install-extras.sh --vision    # saliency-aware cropping
bash scripts/install-extras.sh --buttons   # GPIO buttons
bash scripts/install-extras.sh --ai        # Stable Diffusion XL Turbo
bash scripts/install-extras.sh --all
```

| Tool | What it does | What it needs |
|---|---|---|
| `src/display_picture.py` | Crops to the panel using saliency detection, so a portrait photo keeps its subject instead of being centred blindly | `opencv-contrib-python-headless`, `numpy` |
| `src/display_buttons.py` | The four GPIO buttons on the carrier board: newest / previous / next / shutdown | `python3-libgpiod` **from apt** |
| `src/generate_picture.py` | Generates artwork from the prompt sets in `prompts/` | An OnnxStream build and ~2.5 GB of SDXL Turbo weights |

Three things worth knowing before running `--ai`:

- It needs about **6 GB of free disk**.
- Building XNNPACK on a Pi Zero 2 W takes **hours** and is killed by the OOM
  killer without a 2 GB swap file. The script checks and tells you how to
  raise it. Run it inside `tmux` so an SSH drop does not take it with you.
- Generating one 800×480 image then takes **20–40 minutes** on that board.
  Treat it as a background curiosity, not an interactive feature.

The buttons are **not** started automatically and there is no systemd unit for
them; run `python3 src/display_buttons.py` yourself, or add a unit if you want
them always on.

> The PyPI package named `gpiod` is the libgpiod **v2** API, which is not the
> API `display_buttons.py` uses. The v1 bindings come from Debian as
> `python3-libgpiod`, so either run that script with the system interpreter or
> create the venv with `--system-site-packages`. The script says so if you get
> it wrong rather than failing at the first attribute access.

## Tests

The e-paper panel, NetworkManager, OpenCV and libgpiod are all stubbed, so the
suite runs on any machine — no Pi, no display, no extras installed:

```bash
python3 -m pytest tests/
# or, without pytest:
python3 tests/test_smoke.py && python3 tests/test_weather.py \
  && python3 tests/test_icloud.py && python3 tests/test_extras.py
```

`test_smoke.py` asks every route for a response and checks the security
boundaries (secret redaction, the config write allowlist, same-origin
enforcement, path containment, cookie flags, the network watchdog).
`test_weather.py` and `test_icloud.py` cover the two services that talk to the
internet, with every outbound request answered from canned data — nothing in
`tests/` reaches the network. `test_extras.py` covers the `src/` tools: panel
palettes, GPIO chip discovery, path resolution and the missing-dependency
guards. Run them all before pushing.

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

### iCloud shared album

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/icloud/status` | Album, schedule, last sync, import count |
| POST | `/api/icloud/connect` | Attach an album by link. `{"import_all": true}` also fills the gallery |
| POST | `/api/icloud/disconnect` | Forget the album. Imported photos stay |
| GET | `/api/icloud/photos` | List the album; `?refresh=1` skips the short listing cache |
| POST | `/api/icloud/import` | Import `{"guids": [...]}`, or `{"all": true}`. `{"start_slideshow": true}` starts the slideshow afterwards |
| POST | `/api/icloud/sync` | Pull whatever is new since the last sync |

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
| GET | `/api/remote` | Remote-access state, provider and public address |
| POST | `/api/remote/reconnect` | Force a fresh tunnel |

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
  tokens, the ngrok authtoken — are never returned by an API or rendered into a
  page. The settings form shows a placeholder instead and treats a blank field
  as "keep the stored value".
- The session cookie is marked `Secure` exactly when the connection was HTTPS,
  so it is protected over the tunnel without breaking sign-in over the LAN.
- The pairing hotspot's name and password are derived per device and printed
  only on the e-paper panel. No two units ship joinable with the same
  credentials.

## Service account

The service runs as the unprivileged `vignette` user, not root — it is
reachable from the internet whenever remote access is on. `scripts/install.sh`
creates the account, adds it to `spi`, `gpio` and `netdev`, and installs a
narrow sudo allowlist at `/etc/sudoers.d/vignette` covering only `nmcli`,
`reboot`, `shutdown` and restarting its own unit.

Upgrading an existing root install on a device that is already working, use
the targeted migration rather than the full installer — `install.sh` also runs
`apt-get upgrade` and rebuilds the virtualenv, which are the risky parts and
none of them are the point:

```bash
bash scripts/harden-service.sh --check     # verify, change nothing
bash scripts/harden-service.sh             # migrate
bash scripts/harden-service.sh --rollback  # undo
```

It tests the panel's SPI and GPIO access **as the new account before switching
anything**, backs up the current unit, and restores it automatically if the
service does not come back — a frame that cannot start is a frame you have to
walk over to.

How much can be locked down is capped by one thing: the four privileged actions
(joining a network, rebooting, shutting down, restarting itself) go through
`sudo`, which is setuid, so `NoNewPrivileges` has to stay off. That rules out a
sealed capability set and a `SystemCallFilter` without `@privileged`.
Everything compatible with it is set — read-only `/usr`, `/etc` and `/home`
outside the checkout, a closed device policy admitting only the panel's SPI and
GPIO nodes, and the kernel, clock, hostname and namespace protections.

Even then it is a smaller blast radius, not none: `config.json` holds the WiFi
password and the session key, and the service account can read it. What changes
is that a flaw in image decoding or file serving no longer hands over the whole
board and the LAN behind it.

## Project Structure

```
Vignette/
├── lib/waveshare_epd/           # Waveshare e-paper drivers
│   ├── epd7in3e.py              # 7.3" 6-color panel (E), the default
│   ├── epd7in3f.py              # 7.3" 7-color panel (F); set epd_model
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
│   ├── install-extras.sh        # Optional deps for the src/ tools
│   ├── setup-tunnel.sh          # Point your own domain at the display
│   ├── update.sh                # Remote update script
│   └── vignette.service         # systemd service file
├── tests/
│   ├── test_smoke.py            # Web service; hardware is stubbed
│   ├── test_weather.py          # Weather service; upstream is stubbed
│   ├── test_icloud.py           # iCloud shared albums; upstream is stubbed
│   └── test_extras.py           # The src/ tools; their deps are stubbed
├── output/                      # Image output directory
├── requirements.txt             # Python dependencies
├── requirements-extras.txt      # Dependencies for the src/ tools only
├── LICENSE
└── README.md
```

> `src/` holds standalone CLI tools that the web service never imports. They
> need dependencies the frame itself does not — see **Optional extras** below.

## Credits

- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - E-paper driver
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - Hardware reference

## License

MIT License

<img src="web/static/img/vignette-logo.svg" width="320" alt="Vignette">

&copy; 2026 Vignette

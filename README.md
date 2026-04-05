# Vignette - H System Smart Display

基於 Waveshare 7.3 吋 6 色電子紙的智慧螢幕系統。
A smart display system based on the Waveshare 7.3" 6-color e-paper display.

## 系統架構 / Architecture

```
┌──────────────────────────────────┐
│  手機 / 平板 / 電腦               │
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

## 功能 / Features

- **Web 遠端控制** - 任何裝置透過瀏覽器操作（響應式介面）
- **上傳圖片** - 拖曳上傳、即時預覽電子紙 7 色效果
- **照片導航** - Web 虛擬按鍵瀏覽照片（上一張/下一張/最新）
- **測試圖案** - 一鍵發送 7 色測試圖案驗證硬體
- **遠端管理** - 更新程式、重啟、關機
- **系統監控** - CPU 溫度、記憶體、磁碟、運行時間

## 硬體需求 / Hardware

| 項目 / Item | 規格 / Spec |
|------|------|
| Board | Raspberry Pi Zero 2 W |
| Carrier | Waveshare RPi Zero PhotoPainter Board |
| Display | Waveshare 7.3" 6-color e-paper (800 x 480) |
| Battery | 3.7V 1500mAh Li-Po |
| Storage | 32GB+ microSD (64GB recommended) |

## 快速開始 / Quick Start

### 1. 準備 Raspberry Pi

```bash
# Enable SPI
sudo raspi-config
# → Interface Options → SPI → Enable
```

### 2. 一鍵安裝 / Install

```bash
git clone https://github.com/teyrallc/photopainter.git
cd photopainter
bash scripts/install.sh
```

### 3. 存取 Web 介面 / Access Web UI

```
http://<Pi-IP>:5000
```

## 服務管理 / Service Management

```bash
sudo systemctl status vignette     # 查看狀態 / Status
sudo systemctl restart vignette    # 重啟 / Restart
sudo systemctl stop vignette       # 停止 / Stop
journalctl -u vignette -f          # 日誌 / Logs
```

## API

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

## 專案結構 / Project Structure

```
photopainter/
├── lib/waveshare_epd/           # Waveshare e-paper driver
│   ├── epd7in3e.py              # 7.3" 6-color display driver (E model)
│   └── epdconfig.py             # SPI/GPIO hardware config
├── src/
│   ├── display_picture.py       # Image processing & display (CLI)
│   └── display_buttons.py       # GPIO button controller
├── web/
│   ├── app.py                   # Flask web application
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

## 致謝 / Credits

- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - E-paper driver
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - Hardware reference

## 授權 / License

MIT License

&copy; 2026 Teyra LLC W.Weng

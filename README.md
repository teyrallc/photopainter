# PhotoPainter - Raspberry Pi 電子紙 AI 畫框

基於 Waveshare 7.3 吋 6 色電子紙顯示器的 AI 藝術畫框。

## 系統架構

```
┌──────────────────────────────────┐
│  手機 / 平板 / 電腦 (任何裝置)     │
│  瀏覽器開啟 http://<Pi-IP>:5000   │
└──────────────┬───────────────────┘
               │ WiFi / 區域網路
┌──────────────▼───────────────────┐
│  Raspberry Pi Zero 2 W            │
│  ┌─────────────────────────────┐  │
│  │  Flask Web 伺服器 (port 5000)│  │
│  │  - 上傳/管理圖片            │  │
│  │  - AI 生成控制              │  │
│  │  - 遠端更新程式             │  │
│  │  - 系統監控 (溫度/記憶體)   │  │
│  └──────────┬──────────────────┘  │
│             │                      │
│  ┌──────────▼──────────────────┐  │
│  │  核心程式                    │  │
│  │  - Stable Diffusion (本機)   │  │
│  │  - OpenCV 智慧裁切           │  │
│  │  - Waveshare EPD 驅動        │  │
│  └──────────┬──────────────────┘  │
│             │ SPI                   │
│  ┌──────────▼──────────────────┐  │
│  │  7.3" 6色電子紙 (800x480)   │  │
│  │  黑/白/綠/藍/紅/黃/橘       │  │
│  └─────────────────────────────┘  │
└────────────────────────────────────┘
```

**Pi 上運行的程式**：Web 伺服器 + AI 生成 + 電子紙驅動（systemd 服務，開機自啟）

**Web 介面**：任何同一網路的裝置（手機、平板、電腦）都可透過瀏覽器操作

**遠端更新**：透過 Web 介面或 SSH 即可更新 Pi 上的程式碼

## 功能特色

- **AI 圖片生成**：Stable Diffusion XL Turbo 本機生成（無需雲端）
- **Web 遠端控制**：任何裝置透過瀏覽器操作（手機友善響應式介面）
- **上傳圖片**：拖曳上傳、即時預覽電子紙 7 色效果
- **遠端管理**：一鍵更新程式、重啟、關機
- **系統監控**：CPU 溫度、記憶體、磁碟、運行時間
- **智慧裁切**：OpenCV 顯著性偵測，自動裁切到最重要區域
- **按鍵控制**：實體按鍵瀏覽圖片、關機
- **自動排程**：crontab 定時生成新圖片

## 硬體需求

| 項目 | 規格 |
|------|------|
| 主控板 | Raspberry Pi Zero 2 W |
| 顯示器 | Waveshare 7.3 吋 6 色電子紙 (800 x 480) |
| 記憶卡 | 32GB 以上 microSD (建議 64GB) |
| 電源 | 5V/2A micro USB |
| 散熱 | 建議加裝散熱片 |
| 網路 | WiFi（Web 遠端控制必需） |

## 快速開始

### 步驟 1：準備 Raspberry Pi

1. 燒錄 **Raspbian Bullseye Lite** 到 microSD 卡
2. 啟用 WiFi 和 SSH：

```bash
# 首次開機後 SSH 連入
ssh pi@<Pi的IP位址>
```

3. 啟用 SPI：
```bash
sudo raspi-config
# → Interface Options → SPI → Enable
```

### 步驟 2：一鍵安裝

```bash
git clone https://github.com/teyrallc/photopainter.git
cd photopainter
bash scripts/install.sh
```

安裝腳本會自動完成：
- 安裝所有系統套件和 Python 依賴
- 編譯 OnnxStream（Stable Diffusion 推理引擎）
- 下載 SD XL Turbo 模型（~8GB）
- **設定 systemd 服務（開機自動啟動 Web 伺服器）**

> 安裝需要 2-4 小時（主要是模型下載和編譯）

### 步驟 3：從任何裝置存取 Web 介面

安裝完成後，Web 介面會自動啟動。在**同一 WiFi 網路**下的**任何裝置**上開啟瀏覽器：

```
http://<Pi的IP位址>:5000
```

查看 Pi 的 IP 位址：
```bash
hostname -I
```

## Web 控制台功能

| 頁面 | 功能 |
|------|------|
| **控制台** | 顯示器狀態、系統資訊（溫度/記憶體/IP）、遠端管理（更新/重啟/關機） |
| **上傳圖片** | 拖曳上傳照片、原圖 vs 電子紙效果預覽、一鍵顯示到螢幕 |
| **AI 生成** | 提示詞模板或自訂、參數設定、即時進度追蹤 |
| **圖庫** | 瀏覽所有圖片、預覽/顯示/刪除 |
| **使用手冊** | 完整中文使用指南 |

## 遠端管理

### 方法 1：Web 介面（推薦）

在控制台頁面：
- **遠端更新程式**：從 GitHub 拉取最新程式碼並自動重啟服務
- **重新啟動 Pi**：遠端重啟 Raspberry Pi
- **關機**：遠端安全關機

### 方法 2：SSH

```bash
# 連入 Pi
ssh pi@<Pi的IP位址>

# 更新程式
cd photopainter
bash scripts/update.sh

# 手動管理服務
sudo systemctl status photopainter    # 查看狀態
sudo systemctl restart photopainter   # 重啟服務
sudo systemctl stop photopainter      # 停止服務
journalctl -u photopainter -f         # 查看日誌
```

## 服務管理

安裝後，PhotoPainter 會作為 systemd 服務自動運行：

```bash
# 狀態
sudo systemctl status photopainter

# 重啟
sudo systemctl restart photopainter

# 停止
sudo systemctl stop photopainter

# 開機不自動啟動
sudo systemctl disable photopainter

# 查看即時日誌
journalctl -u photopainter -f
```

## 命令列使用（進階）

除了 Web 介面外，也可以透過 SSH 使用命令列：

```bash
cd photopainter
source venv/bin/activate

# 生成 AI 圖片（約 30 分鐘）
python src/generate_picture.py output/

# 自訂提示詞
python src/generate_picture.py output/ --prompt "a beautiful sunset as an oil painting"

# 使用風景模板
python src/generate_picture.py output/ --prompts prompts/landscapes.json

# 顯示圖片到電子紙
python src/display_picture.py output/output.png

# 模擬模式（無需硬體，用於測試）
python src/display_picture.py image.png -s -o preview.png
```

## API 文件

Web 伺服器提供 RESTful API，可用於整合其他系統或自動化：

### 圖片操作
```bash
# 上傳圖片
curl -F "file=@photo.jpg" http://<Pi-IP>:5000/api/upload

# 顯示到電子紙
curl -X POST -H "Content-Type: application/json" \
  -d '{"filename": "photo.jpg"}' http://<Pi-IP>:5000/api/display

# 7 色效果預覽
curl http://<Pi-IP>:5000/api/preview/photo.jpg -o preview.png

# 列出所有圖片
curl http://<Pi-IP>:5000/api/images

# 刪除圖片
curl -X DELETE http://<Pi-IP>:5000/api/images/photo.jpg
```

### AI 生成
```bash
# 開始生成
curl -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "sunset", "steps": 5}' http://<Pi-IP>:5000/api/generate

# 查詢進度
curl http://<Pi-IP>:5000/api/generate/status
```

### 螢幕控制
```bash
# 清除螢幕
curl -X POST http://<Pi-IP>:5000/api/clear

# 螢幕休眠
curl -X POST http://<Pi-IP>:5000/api/sleep
```

### 系統管理
```bash
# 系統資訊（溫度、記憶體、IP、版本）
curl http://<Pi-IP>:5000/api/system/info

# 系統狀態
curl http://<Pi-IP>:5000/api/status

# 遠端更新程式
curl -X POST http://<Pi-IP>:5000/api/system/update

# 遠端重啟
curl -X POST http://<Pi-IP>:5000/api/system/reboot

# 遠端關機
curl -X POST http://<Pi-IP>:5000/api/system/shutdown
```

## 自動排程

```bash
crontab -e

# 每天早上 8 點生成新圖片並顯示
0 8 * * * cd /home/pi/photopainter && venv/bin/python src/generate_picture.py output/ && venv/bin/python src/display_picture.py output/output.png

# 每 6 小時生成一次
0 */6 * * * cd /home/pi/photopainter && venv/bin/python src/generate_picture.py output/ && venv/bin/python src/display_picture.py output/output.png
```

> 注意：Web 介面已透過 systemd 設定為開機自啟，不需要在 crontab 加入。

## 按鍵功能

| 按鍵 | GPIO | 功能 |
|------|------|------|
| A | GPIO 5 | 顯示最新圖片 |
| B | GPIO 6 | 上一張 |
| C | GPIO 16 | 下一張 |
| D | GPIO 24 | 關機 |

```bash
python src/display_buttons.py
```

## 專案結構

```
photopainter/
├── lib/waveshare_epd/           # Waveshare 電子紙驅動程式
│   ├── epd7in3f.py              # 7.3 吋 6 色顯示器驅動
│   └── epdconfig.py             # SPI/GPIO 硬體配置
├── src/
│   ├── generate_picture.py      # AI 圖片生成（OnnxStream）
│   ├── display_picture.py       # 圖片處理與電子紙顯示
│   └── display_buttons.py       # GPIO 按鍵控制
├── web/
│   ├── app.py                   # Flask Web 應用程式（主程式）
│   ├── templates/               # HTML 頁面模板
│   └── static/                  # CSS/JS 靜態檔案
├── prompts/                     # AI 提示詞模板
├── scripts/
│   ├── install.sh               # 一鍵安裝腳本
│   ├── update.sh                # 遠端更新腳本
│   └── photopainter.service     # systemd 服務檔（參考）
├── output/                      # 圖片輸出目錄
├── requirements.txt             # Python 依賴
└── README.md                    # 本文件
```

## 故障排除

### 電子紙無反應
1. 確認 SPI 已啟用：`sudo raspi-config` → Interface Options → SPI
2. 檢查排線連接
3. 測試驅動：
```bash
python -c "import sys; sys.path.insert(0,'lib'); from waveshare_epd import epd7in3f; e=epd7in3f.EPD(); e.init(); e.Clear(); e.sleep()"
```

### AI 生成失敗
1. 確認 OnnxStream 二進位檔：`ls OnnxStream/src/build/sd`
2. 確認模型檔案：`ls models/stable-diffusion-xl-turbo-1.0-anyshape-onnxstream/`
3. 確認 swap：`free -h`（需要 1024MB）
4. 確認磁碟空間：`df -h`

### Web 無法從其他裝置存取
1. 確認服務運行中：`sudo systemctl status photopainter`
2. 確認 Pi 的 IP：`hostname -I`
3. 確認 Pi 和你的裝置在同一 WiFi 網路
4. 確認 port 5000 未被防火牆阻擋：`sudo ufw allow 5000`（如有啟用 ufw）
5. 嘗試本機存取：`curl http://localhost:5000`

### 遠端更新失敗
1. 確認 Pi 有網路連線：`ping google.com`
2. 確認 git remote：`git remote -v`
3. 手動更新：`cd photopainter && git pull && sudo systemctl restart photopainter`

## 致謝

- [PaperPiAI](https://github.com/dylski/PaperPiAI) - 原始 AI 電子紙專案
- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - 電子紙驅動程式
- [OnnxStream](https://github.com/vitoplantamura/OnnxStream) - Stable Diffusion 推理框架
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - 硬體參考

## 授權

MIT License

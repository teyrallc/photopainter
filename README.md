# PhotoPainter - Raspberry Pi 電子紙 AI 畫框

基於 Waveshare 7.3 吋 6 色電子紙顯示器的 AI 藝術畫框，能夠在 Raspberry Pi Zero 2 上本機執行 Stable Diffusion 生成 AI 藝術作品，並透過 Web 介面進行控制。

## 功能特色

- **AI 圖片生成**：使用 Stable Diffusion XL Turbo 在本機生成藝術作品（無需網路）
- **Web 控制介面**：透過瀏覽器上傳圖片、AI 生成、預覽電子紙效果
- **7 色電子紙顯示**：支援黑、白、綠、藍、紅、黃、橘七種顏色
- **智慧裁切**：使用 OpenCV 顯著性偵測，自動裁切到最重要的區域
- **按鍵控制**：實體按鍵瀏覽圖片、關機
- **自動排程**：支援 crontab 定時生成新圖片

## 硬體需求

| 項目 | 規格 |
|------|------|
| 主控板 | Raspberry Pi Zero 2 W |
| 顯示器 | Waveshare 7.3 吋 6 色電子紙 (800 x 480) |
| 記憶卡 | 32GB 以上 microSD (建議 64GB) |
| 電源 | 5V/2A micro USB |
| 散熱 | 建議加裝散熱片 |

## 快速開始

### 1. 安裝

```bash
# 下載專案
git clone https://github.com/teyrallc/photopainter.git
cd photopainter

# 執行一鍵安裝（需要 2-4 小時）
bash scripts/install.sh
```

安裝包含：系統套件、Python 環境、OnnxStream 編譯、SD XL Turbo 模型下載（~8GB）。

### 2. 啟動 Web 介面

```bash
source venv/bin/activate
python web/app.py
```

在同一網路的裝置上開啟瀏覽器：`http://<Pi的IP位址>:5000`

### 3. 命令列使用

```bash
source venv/bin/activate

# 生成 AI 圖片（約 30 分鐘）
python src/generate_picture.py output/

# 自訂提示詞生成
python src/generate_picture.py output/ --prompt "a beautiful sunset as an oil painting"

# 顯示圖片到電子紙
python src/display_picture.py output/output.png

# 模擬模式（無需硬體）
python src/display_picture.py image.png -s -o preview.png
```

## 專案結構

```
photopainter/
├── lib/waveshare_epd/       # Waveshare 電子紙驅動程式
│   ├── epd7in3f.py          # 7.3 吋 6 色顯示器驅動
│   └── epdconfig.py         # SPI/GPIO 硬體配置
├── src/
│   ├── generate_picture.py  # AI 圖片生成
│   ├── display_picture.py   # 圖片處理與顯示
│   └── display_buttons.py   # 按鍵控制
├── web/
│   ├── app.py               # Flask Web 應用程式
│   ├── templates/           # HTML 模板
│   └── static/              # CSS/JS 靜態檔案
├── prompts/                 # 提示詞模板
├── scripts/install.sh       # 安裝腳本
└── output/                  # 圖片輸出目錄
```

## Web 控制台功能

| 頁面 | 功能 |
|------|------|
| 控制台 | 顯示器狀態、快速操作、最近圖片 |
| 上傳圖片 | 拖曳上傳、原圖/電子紙效果預覽、一鍵顯示 |
| AI 生成 | 提示詞模板/自訂、參數設定、即時進度 |
| 圖庫 | 瀏覽所有圖片、預覽/顯示/刪除 |
| 使用手冊 | 完整使用指南 |

## API

Web 介面提供 RESTful API：

```bash
# 上傳圖片
curl -F "file=@photo.jpg" http://pi:5000/api/upload

# 顯示到電子紙
curl -X POST -H "Content-Type: application/json" \
  -d '{"filename": "photo.jpg"}' http://pi:5000/api/display

# AI 生成
curl -X POST -H "Content-Type: application/json" \
  -d '{"prompt": "sunset", "steps": 5}' http://pi:5000/api/generate

# 查詢狀態
curl http://pi:5000/api/generate/status

# 7 色預覽
curl http://pi:5000/api/preview/photo.jpg -o preview.png
```

## 自動排程

```bash
crontab -e

# 每天 8 點生成並顯示
0 8 * * * cd /home/pi/photopainter && venv/bin/python src/generate_picture.py output/ && venv/bin/python src/display_picture.py output/output.png

# 開機自動啟動 Web
@reboot cd /home/pi/photopainter && venv/bin/python web/app.py &
```

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

## 故障排除

### 電子紙無反應
1. 確認 SPI 已啟用：`sudo raspi-config` → Interface Options → SPI
2. 檢查排線連接
3. 測試驅動：`python -c "import sys; sys.path.insert(0,'lib'); from waveshare_epd import epd7in3f; e=epd7in3f.EPD(); e.init(); e.Clear(); e.sleep()"`

### AI 生成失敗
1. 確認 OnnxStream 二進位檔：`ls OnnxStream/src/build/sd`
2. 確認模型檔案：`ls models/stable-diffusion-xl-turbo-1.0-anyshape-onnxstream/`
3. 確認 swap：`free -h`（需要 1024MB）
4. 確認磁碟空間：`df -h`

### Web 無法存取
1. 確認伺服器運行中：`python web/app.py`
2. 確認 IP：`hostname -I`
3. 確認 port 5000 開放

## 致謝

- [PaperPiAI](https://github.com/dylski/PaperPiAI) - 原始 AI 電子紙專案
- [Waveshare e-Paper](https://github.com/waveshare/e-Paper) - 電子紙驅動程式
- [OnnxStream](https://github.com/vitoplantamura/OnnxStream) - Stable Diffusion 推理框架
- [Waveshare RPi Zero PhotoPainter](https://www.waveshare.net/wiki/RPi_Zero_PhotoPainter) - 硬體參考

## 授權

MIT License

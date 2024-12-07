# 2024 年度影片評選網站

一個專門用於影片上傳、播放和投票的網站平台。使用者可以上傳影片並對喜歡的影片進行投票。

## 功能特點

- 影片上傳

  - 支持中文檔名
  - 自動影片壓縮
  - 每個 IP 限制上傳一次
  - 支持格式：MP4、MOV、AVI
  - 大小限制：100MB

- 📺 影片播放

  - 自動播放功能
  - 點擊暫停/播放
  - 左右按鈕切換影片
  - 頂部進度條顯示

- 👍 投票系統

  - 每個 IP 對每個影片只能投一次
  - 可收回投票
  - 即時更新投票數

- 👨‍💼 管理功能
  - 管理員登入介面
  - 影片管理（刪除）
  - 觀看數據統計

## 技術架構

- 後端：Python Flask
- 前端：HTML5, CSS3, JavaScript
- 影片處理：FFmpeg
- 認證：HTTP Basic Auth

## 安裝說明

1. 安裝必要套件：

```bash
pip install -r requirements.txt
```

2. 安裝 FFmpeg：

#### Ubuntu/Debian

```bash
sudo apt-get install ffmpeg
```

#### MacOS

```bash
brew install ffmpeg
```

#### Windows

下載 FFmpeg 並添加到系統環境變量

3. 設置環境變數：

```bash
export PATH="/usr/local/opt/ffmpeg/bin:$PATH"
```

4. 運行應用：

```bash
python app.py
```

## 使用說明

1. 首頁功能：

   - 上傳影片：點擊右下角上傳按鈕
   - 播放控制：點擊影片暫停/播放
   - 切換影片：使用左右導航按鈕
   - 投票功能：點擊投票按鈕，再次點擊可收回

2. 管理員功能：
   - 訪問路徑：`http://localhost:5000/admin`
   - 登入資訊：
     - 用戶名：admin
     - 密碼：設置的環境變數
   - 功能：查看統計、刪除影片

## 目錄結構

```bash
project/
├── app.py # 主應用程式
├── requirements.txt # 依賴套件
├── .gitignore # Git 忽略文件
├── Procfile # Heroku 部署文件
├── templates/ # 模板文件
│ └── index.html # 主頁面
└── uploads/ # 上傳的影片存放處
```

## 注意事項

- 確保有足夠的硬碟空間存放影片
- 定期備份 upload_records.json
- 請勿刪除 uploads 目錄
- 建議定期清理舊影片
- 上傳大檔案時需要耐心等待壓縮完成

## 開發者說明

- 影片壓縮使用 FFmpeg，可在 app.py 中調整壓縮參數
- 投票記錄使用 IP 位址識別，存儲在 upload_records.json
- 支持跨平台運行（Windows/Mac/Linux）

## 授權協議

MIT License

## 作者

[H8Y8]

## 更新日誌

### v1.0.0 (2024-12-08)

- 初始版本發布
- 實現基本功能：上傳、播放、投票
- 添加管理員介面
- 支持影片壓縮

## 待實現功能

### 資料庫遷移

- 將本地存儲遷移至 Firebase
  - 影片存儲遷移至 Firebase Storage
  - 用戶數據遷移至 Firestore
  - 投票記錄遷移至 Firestore
  - 觀看記錄遷移至 Firestore

### 部署優化

- Heroku 部署
  - 設置環境變數
  - 配置 Procfile
  - 處理靜態文件
  - 優化應用性能

### 社交功能

- 影片評論系統
  - 基本評論功能

### 預計發布時程

- v1.1.0: Firebase 整合 (2024-12-9)
- v1.2.0: Heroku 部署 (2024-12-10)
- v1.3.0: 評論功能 (2024-12-11)

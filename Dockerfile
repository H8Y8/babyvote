# 使用 Python 3.9 作為基礎映像
FROM python:3.9-slim

# 安裝 FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 設置工作目錄
WORKDIR /app

# 複製需要的文件
COPY requirements.txt .
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# 創建上傳目錄
RUN mkdir uploads

# 安裝 Python 依賴
RUN pip install --no-cache-dir -r requirements.txt

# 設置環境變數
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV ADMIN_PASSWORD=admin123

# 開放端口
EXPOSE 5000

# 運行應用
CMD ["python", "app.py"] 
# 使用 Python 3.9 作為基礎映像
FROM python:3.9-slim

# 安裝 FFmpeg 和 Redis 客戶端
RUN apt-get update && \
    apt-get install -y ffmpeg redis-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 設置工作目錄
WORKDIR /app

# 複製所有必要文件
COPY requirements.txt .
COPY app.py .
COPY config.py .
COPY models.py .
COPY tasks.py .
COPY extensions.py .
COPY templates/ templates/

# 創建必要的目錄
RUN mkdir uploads
RUN mkdir instance

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
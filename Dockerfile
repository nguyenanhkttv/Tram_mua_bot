FROM python:3.10-slim

# Cài đặt Chromium và các thư viện hệ thống cần thiết
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Thiết lập biến môi trường chỉ định Chrome
ENV CHROME_BIN=/usr/bin/chromium

WORKDIR /app

# Cài đặt Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code
COPY . .

# Chạy ứng dụng
CMD ["python", "app.py"]

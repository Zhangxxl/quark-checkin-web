FROM python:3.12-slim

WORKDIR /app

# 系统依赖（仅 ca-certificates 用于 HTTPS，无需编译器）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖以利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ .

# 数据卷挂载点（SQLite + 日志）
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

EXPOSE 5000

# 通过环境变量配置：SIGN_HOUR / SIGN_MINUTE / SERVERCHAN_SENDKEY / NOTIFY_MODE / PORT
CMD ["python", "app.py"]

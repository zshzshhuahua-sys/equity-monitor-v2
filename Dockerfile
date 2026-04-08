FROM python:3.12-slim

WORKDIR /app

# 系统依赖：pdfplumber 需要 cjk，故安装字体
RUN apt-get update && apt-get install -y \
    fonts-noto-cjk \
    curl \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f -v

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY src/ ./src/
COPY web/ ./web/
COPY config/ ./config/
COPY templates/ ./templates/
COPY data/ ./data/

# 重建字体缓存（确保 pdfplumber 能找到中文字体）
RUN fc-cache -f -v || true

# 启动命令
ENV PYTHONPATH=/app
ENV PROJECT_ROOT=/app
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/equity_monitor.db

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]

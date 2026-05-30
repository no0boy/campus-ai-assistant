FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install dashscope

# 复制代码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# 暴露端口
EXPOSE 8000

# 启动（API Key 通过环境变量注入）
CMD uvicorn main:app --host 0.0.0.0 --port 7860

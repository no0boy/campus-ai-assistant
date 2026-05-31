FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install dashscope

# 复制代码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# 从模板生成 config.py（API Key 通过环境变量注入）
RUN cp config.example.py config.py

# 暴露端口
EXPOSE 7860

# 启动
CMD uvicorn main:app --host 0.0.0.0 --port 7860

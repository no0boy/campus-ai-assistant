"""
配置模板 — 复制此文件为 config.py 并填入真实的 API Key
真实 config.py 已在 .gitignore 中排除，不会上传到 GitHub
"""

import os

# ========== 数据库 ==========
DATABASE_URL = "sqlite:///./campus_ai.db"

# ========== JWT ==========
SECRET_KEY = "your-secret-key-change-this-in-production"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 72

# ========== 大模型（通义千问） ==========
# 去 https://bailian.console.aliyun.com 免费领取 API Key
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "你的阿里云百炼API_Key")
LLM_MODEL = "qwen-plus"
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_TEMPERATURE = 0.7
LLM_MAX_TOKENS = 2048

# Embedding 模型
EMBEDDING_MODEL = "text-embedding-v3"

# ========== ChromaDB ==========
CHROMA_PERSIST_DIR = "./chroma_data"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K_RETRIEVAL = 3
SIMILARITY_THRESHOLD = 0.5

# ========== CORS ==========
CORS_ORIGINS = ["*"]

# ========== 上传 ==========
UPLOAD_DIR = "./uploads"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".txt"}

"""
配置模板 — 复制此文件为 config.py 并填入真实的 API Key
真实 config.py 已在 .gitignore 中排除，不会上传到 GitHub

使用方式：
  1. cp config.example.py config.py
  2. 设置环境变量 DASHSCOPE_API_KEY=你的Key
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
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.6-plus")
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_TEMPERATURE = 0.7
LLM_MAX_TOKENS = 2048

# Embedding 模型
EMBEDDING_MODEL = "text-embedding-v3"

# ========== ChromaDB 向量库 ==========
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

# ========== 缓存配置 ==========
CACHE_ENABLED = True
CACHE_MAX_SIZE = 500
CACHE_TTL_HOURS = 24

# ========== 对话压缩 ==========
MAX_HISTORY_TOKENS = 3000
KEEP_RECENT_ROUNDS = 2
SUMMARY_MODEL = "qwen-turbo"
SUMMARY_MAX_TOKENS = 200

# ========== 上下文优化 ==========
MAX_SOURCE_CHARS = 400
TRIM_OVERLAP_SOURCES = True

# ========== 混合检索 ==========
HYBRID_SEARCH_ENABLED = True
BM25_TOP_K = 10
RRF_K = 60
FINAL_TOP_K = 5

# ========== 模型定价表（元/1K tokens）==========
MODEL_PRICING = {
    "qwen-turbo":           {"input": 0.0003, "output": 0.0006},
    "qwen-plus":            {"input": 0.0008, "output": 0.002},
    "qwen3.6-plus":         {"input": 0.0008, "output": 0.002},
    "qwen-max":             {"input": 0.002,  "output": 0.006},
    "claude-haiku":         {"input": 0.0008, "output": 0.004},
    "claude-sonnet":        {"input": 0.003,  "output": 0.015},
    "claude-opus":          {"input": 0.015,  "output": 0.075},
    "deepseek-chat":        {"input": 0.001,  "output": 0.002},
    "deepseek-reasoner":    {"input": 0.001,  "output": 0.002},
    "gemini-flash":         {"input": 0.0003, "output": 0.0006},
    "gemini-pro":           {"input": 0.001,  "output": 0.004},
    "ollama":               {"input": 0.0,    "output": 0.0},
    "llama":                {"input": 0.0,    "output": 0.0},
    "qwen2.5":              {"input": 0.0,    "output": 0.0},
    "default":              {"input": 0.001,  "output": 0.002},
}

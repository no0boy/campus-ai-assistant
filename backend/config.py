"""
全局配置
所有配置项集中管理，敏感信息从环境变量读取
"""

import os

# ========== 数据库 ==========
DATABASE_URL = "sqlite:///./campus_ai.db"

# ========== JWT ==========
SECRET_KEY = "campus-ai-secret-key-change-in-production"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 72  # token 3天过期

# ========== 大模型（通义千问） ==========
# API Key — 必须通过环境变量设置，不硬编码
# Windows: set DASHSCOPE_API_KEY=sk-xxx
# Mac/Linux: export DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_TEMPERATURE = 0.7
LLM_MAX_TOKENS = 2048

# Embedding 模型
EMBEDDING_MODEL = "text-embedding-v3"

# ========== ChromaDB 向量库 ==========
CHROMA_PERSIST_DIR = "./chroma_data"
CHUNK_SIZE = 500       # 文本切片大小
CHUNK_OVERLAP = 50     # 切片重叠字数
TOP_K_RETRIEVAL = 3    # 检索返回 Top-K 个片段
SIMILARITY_THRESHOLD = 0.5  # 相似度阈值，低于此值视为"知识库无相关内容"

# ========== CORS 跨域（前端开发用） ==========
CORS_ORIGINS = ["*"]

# ========== 上传配置 ==========
UPLOAD_DIR = "./uploads"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".pdf", ".txt"}

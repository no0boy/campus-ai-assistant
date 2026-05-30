"""
校园 AI 助手 — FastAPI 主入口
启动命令: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from database import init_db
from routes import auth, chat, documents, stats, settings
import config

# ========== 创建 FastAPI 应用 ==========
app = FastAPI(
    title="校园 AI 助手 API",
    description="基于 RAG 知识库的校园智能问答系统",
    version="1.0.0"
)

# ========== 配置 CORS 跨域 ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 注册路由 ==========
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(stats.router)
app.include_router(settings.router)


# ========== 启动事件 ==========
@app.on_event("startup")
def startup():
    """应用启动时初始化数据库"""
    print("[INFO] 校园 AI 助手启动中...")
    init_db()
    print("[OK] 数据库初始化完成")
    print(f"[INFO] API 文档: http://127.0.0.1:8000/docs")
    print(f"[INFO] 健康检查: http://127.0.0.1:8000/health")


@app.get("/health")
def health_check():
    """健康检查接口"""
    return {"status": "ok", "message": "校园 AI 助手服务运行中"}


@app.get("/")
def root():
    return {
        "name": "校园 AI 助手",
        "version": "1.0.0",
        "docs": "/docs"
    }


# ========== 启动入口 ==========
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )

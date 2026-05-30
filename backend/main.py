"""
校园 AI 助手 — FastAPI 主入口（一体部署版：API + 前端）
启动命令: uvicorn main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import uvicorn
import os

from database import init_db
from routes import auth, chat, documents, stats, settings
import config

# ========== 创建 FastAPI 应用 ==========
app = FastAPI(
    title="校园 AI 助手 API",
    description="基于 RAG 知识库的校园智能问答系统",
    version="1.0.0"
)

# ========== CORS ==========
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== API 路由 ==========
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(stats.router)
app.include_router(settings.router)

# ========== 前端静态文件（部署用） ==========
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/")
def root():
    if os.path.exists(FRONTEND_DIR):
        return RedirectResponse(url="/app/student/login.html")
    return {"name": "campus-ai-assistant", "docs": "/docs"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ========== 启动 ==========
@app.on_event("startup")
def startup():
    print("[INFO] 启动中...")
    init_db()
    print("[OK] 数据库就绪")
    print(f"[INFO] 前端: http://0.0.0.0:8000/app/student/login.html")
    print(f"[INFO] API文档: http://0.0.0.0:8000/docs")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

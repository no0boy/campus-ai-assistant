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
from routes import usage, user, webhook
import config

# ========== 创建 FastAPI 应用 ==========
app = FastAPI(
    title="校园AI知识平台 API",
    description="基于 RAG 知识库的校园智能问答系统",
    version="3.0.0"
)

# ========== 请求日志中间件 ==========
@app.middleware("http")
async def log_requests(request, call_next):
    import time, uuid
    rid = str(uuid.uuid4())[:8]
    start = time.time()
    response = await call_next(request)
    elapsed = int((time.time() - start) * 1000)
    # 只打 API 请求日志
    if "/api/" in str(request.url.path):
        print(f"[API] rid={rid} {request.method} {request.url.path} → {response.status_code} ({elapsed}ms)")
    response.headers["X-Request-ID"] = rid
    return response

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
app.include_router(usage.router)
app.include_router(user.router)
app.include_router(webhook.router)

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

    # 自动导入种子文档（知识库为空时）
    seeds_dir = os.path.join(os.path.dirname(__file__), "seeds")
    if os.path.exists(seeds_dir):
        try:
            from services.rag_service import process_document, collection
            from database import SessionLocal, Document
            existing = collection.count()
            if existing == 0:
                print("[INFO] 知识库为空，自动导入种子文档...")
                import_count = 0
                db2 = SessionLocal()
                for f in os.listdir(seeds_dir):
                    fp = os.path.join(seeds_dir, f)
                    chunk_count = 0
                    if f.endswith(".txt"):
                        chunk_count = process_document(fp, "txt", f)
                    elif f.endswith(".pdf"):
                        chunk_count = process_document(fp, "pdf", f)
                    else:
                        continue
                    # 同步写入 SQLite 文档表
                    doc = Document(
                        title=f,
                        file_path=fp,
                        file_type="txt" if f.endswith(".txt") else "pdf",
                        chunk_count=chunk_count,
                        uploader_id=1  # admin
                    )
                    db2.add(doc)
                    db2.commit()
                    import_count += 1
                db2.close()
                print(f"[OK] 已导入 {import_count} 篇种子文档")
            else:
                print(f"[INFO] 知识库已有 {existing} 条向量，跳过导入")
        except Exception as e:
            print(f"[WARN] 种子文档导入失败: {e}")

    # 构建 BM25 索引
    try:
        from services.hybrid_search import build_index
        bm25_count = build_index()
        print(f"[OK] BM25 索引就绪 ({bm25_count} 条)")
    except Exception as e:
        print(f"[WARN] BM25 索引跳过: {e}")

    print(f"[INFO] 前端: http://0.0.0.0:8000/app/student/login.html")
    print(f"[INFO] API文档: http://0.0.0.0:8000/docs")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

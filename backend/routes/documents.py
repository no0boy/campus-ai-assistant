"""
文档管理模块 — 上传、列表、删除、统计、预览
"""

from fastapi import APIRouter, Depends, UploadFile, File, Query
from typing import Optional
import os
import shutil

from routes.auth import get_current_user
from database import get_db, User, Document, UsageLog
from services.rag_service import process_document, delete_document_vectors, get_all_documents_info
from services.hybrid_search import build_index
from sqlalchemy.orm import Session
from sqlalchemy import func
import config

router = APIRouter(prefix="/api/documents", tags=["文档"])


@router.post("/upload")
async def upload_doc(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """上传文档：保存文件 → 解析 → 切片 → 向量化"""

    # 1. 校验文件类型
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        return {"code": 400, "message": f"不支持的文件类型 {ext}，仅支持 PDF 和 TXT"}

    # 2. 保存文件
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(config.UPLOAD_DIR, file.filename)

    # 避免文件名冲突
    if os.path.exists(file_path):
        name, ext2 = os.path.splitext(file.filename)
        import time
        file_path = os.path.join(config.UPLOAD_DIR, f"{name}_{int(time.time())}{ext2}")

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 3. 解析 + 切片 + 向量化
    try:
        file_type = ext.replace(".", "")
        chunk_count = process_document(file_path, file_type, file.filename)
    except Exception as e:
        return {"code": 500, "message": f"文档处理失败: {str(e)}"}

    # 4. 记录到数据库
    doc = Document(
        title=file.filename,
        file_path=file_path,
        file_type=file_type,
        chunk_count=chunk_count,
        uploader_id=user.id
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 重建 BM25 索引
    build_index()

    return {
        "code": 0,
        "message": f"上传成功，已自动切片为 {chunk_count} 个片段并完成向量化",
        "data": {
            "id": doc.id,
            "title": doc.title,
            "file_type": doc.file_type,
            "chunk_count": doc.chunk_count,
            "access_count": doc.access_count,
            "created_at": doc.created_at.isoformat() if doc.created_at else ""
        }
    }


@router.get("")
def list_docs(
    search: Optional[str] = Query(None, description="搜索文档名"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取文档列表（支持搜索）"""

    query = db.query(Document)
    if search:
        query = query.filter(Document.title.contains(search))
    db_docs = query.order_by(Document.created_at.desc()).all()

    # 从 ChromaDB 补充实时信息
    chroma_info = {d["title"]: d["chunk_count"] for d in get_all_documents_info()}

    docs = []
    for d in db_docs:
        docs.append({
            "id": d.id,
            "title": d.title,
            "file_type": d.file_type,
            "chunk_count": chroma_info.get(d.title, d.chunk_count),
            "access_count": d.access_count or 0,
            "created_at": d.created_at.isoformat() if d.created_at else ""
        })

    return {"code": 0, "data": docs}


@router.get("/stats")
def doc_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """文档引用统计排行"""
    docs = db.query(Document)\
        .order_by(Document.access_count.desc())\
        .all()

    total_access = sum(d.access_count or 0 for d in docs)

    stats = []
    for d in docs:
        ratio = round((d.access_count or 0) / total_access * 100, 1) if total_access > 0 else 0
        stats.append({
            "id": d.id,
            "title": d.title,
            "access_count": d.access_count or 0,
            "ratio": ratio,
            "chunk_count": d.chunk_count,
            "created_at": d.created_at.isoformat() if d.created_at else "",
        })

    return {"code": 0, "data": stats}


@router.get("/{doc_id}/chunks")
def preview_chunks(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """预览文档切片内容"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return {"code": 404, "message": "文档不存在"}

    from services.rag_service import collection
    doc_prefix = doc.title.replace(" ", "_").replace(".", "_")
    try:
        results = collection.get(include=["documents", "metadatas"])
        chunks = []
        for i, did in enumerate(results.get("ids", [])):
            if did.startswith(doc_prefix):
                chunks.append({
                    "chunk_id": did,
                    "index": results["metadatas"][i].get("chunk_index", i) if results.get("metadatas") else i,
                    "content": results["documents"][i][:500] if results.get("documents") else "",
                })

        chunks.sort(key=lambda x: x["index"])
        return {"code": 0, "data": {"title": doc.title, "chunks": chunks}}
    except Exception as e:
        return {"code": 500, "message": f"读取切片失败: {str(e)}"}


@router.post("/batch-delete")
def batch_delete(
    ids: list[int],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量删除文档"""
    if user.role != "admin":
        return {"code": 403, "message": "仅管理员可删除"}

    deleted = 0
    for doc_id in ids:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            continue
        delete_document_vectors(doc.title)
        if os.path.exists(doc.file_path):
            os.remove(doc.file_path)
        db.delete(doc)
        deleted += 1

    db.commit()
    build_index()

    return {"code": 0, "message": f"已删除 {deleted} 篇文档"}


@router.delete("/{doc_id}")
def delete_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除单个文档（同时删除向量和文件）"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return {"code": 404, "message": "文档不存在"}

    # 1. 删除 ChromaDB 中的向量
    delete_document_vectors(doc.title)

    # 2. 删除本地文件
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    # 3. 删除数据库记录
    db.delete(doc)
    db.commit()

    # 重建 BM25 索引
    build_index()

    return {"code": 0, "message": "文档已删除"}

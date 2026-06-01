"""
聊天模块 — AI 问答接口（支持 SSE 流式输出）
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json

from routes.auth import get_current_user
from database import get_db, User, Conversation, UsageLog, Document
from services.rag_service import ask, ask_stream, ask_with_agent, ask_stream_with_agent
from services.token_tracker import hash_question, count_tokens
import config as _cfg
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/chat", tags=["聊天"])


class AskRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    stream: bool = False


@router.post("/ask")
def chat_ask(req: AskRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI 问答接口（支持 SSE 流式）"""

    # 获取历史对话
    history = []
    if req.conversation_id:
        records = db.query(Conversation)\
            .filter(Conversation.conversation_id == req.conversation_id)\
            .order_by(Conversation.created_at.asc())\
            .all()
        for r in records:
            history.append({"role": "user", "content": r.question})
            history.append({"role": "ai", "content": r.answer})

    # 流式输出
    if req.stream:
        def generate():
            full_answer = ""
            sources = []
            search_method = "vector"
            llm_available = True

            for chunk in ask_stream_with_agent(req.question, history):
                kind = chunk.get("type")

                if kind == "agent":
                    agent_info = {"name": chunk.get("name", ""), "emoji": chunk.get("emoji", "🤖")}
                    yield f"data: {json.dumps({'type':'agent','name':agent_info['name'],'emoji':agent_info['emoji']}, ensure_ascii=False)}\n\n"

                elif kind == "meta":
                    sources = chunk.get("sources", [])
                    search_method = chunk.get("search_method", "vector")
                    yield f"data: {json.dumps({'type':'meta','sources':len(sources),'search_method':search_method}, ensure_ascii=False)}\n\n"

                elif kind == "chunk":
                    text = chunk.get("text", "")
                    full_answer += text
                    yield f"data: {json.dumps({'type':'chunk','text':text}, ensure_ascii=False)}\n\n"

                elif kind == "error":
                    llm_available = False
                    yield f"data: {json.dumps({'type':'error','msg':chunk.get('msg','')}, ensure_ascii=False)}\n\n"

                elif kind == "done":
                    # 保存对话
                    conv = Conversation(
                        user_id=user.id,
                        conversation_id=req.conversation_id or f"conv_{user.id}_{hash(req.question)}",
                        question=req.question,
                        answer=full_answer,
                        sources=sources
                    )
                    db.add(conv)
                    db.commit()

                    # 写入 Token 消耗日志
                    usage = chunk.get("usage", {})
                    usage_log = UsageLog(
                        user_id=user.id,
                        username=user.username,
                        question=req.question,
                        question_hash=hash_question(req.question),
                        model_name=usage.get("model_name", ""),
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        cost=usage.get("cost", 0.0),
                        response_time_ms=usage.get("response_time_ms", 0),
                        search_method=search_method,
                        source_count=chunk.get("source_count", len(sources)),
                        cached=1 if usage.get("cached") else 0,
                        success=1 if llm_available else 0,
                        error_msg=chunk.get("llm_error", ""),
                    )
                    db.add(usage_log)
                    db.commit()

                    # 累加文档引用计数
                    for s in sources:
                        doc_title = s.get("title", "")
                        if doc_title:
                            doc = db.query(Document).filter(Document.title == doc_title).first()
                            if doc:
                                doc.access_count = (doc.access_count or 0) + 1
                    db.commit()

                    # 更新用户长期记忆
                    _update_memory(user, req.question, full_answer, db)

                    done_data = {
                        'type':'done',
                        'conversation_id':conv.conversation_id,
                        'sources':[{'title':s['title'],'content':s['content'][:300],'score':s.get('score',0)} for s in sources],
                        'search_method':search_method,
                        'llm_available':llm_available,
                        'agent': chunk.get('agent', {}),
                    }
                    yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # 普通模式（Agent 路由）
    result = ask_with_agent(req.question, history)
    conv = Conversation(
        user_id=user.id,
        conversation_id=req.conversation_id or f"conv_{user.id}_{hash(req.question)}",
        question=req.question,
        answer=result["answer"],
        sources=result["sources"]
    )
    db.add(conv)
    db.commit()

    # 写入 Token 消耗日志
    usage = result.get("usage", {})
    usage_log = UsageLog(
        user_id=user.id,
        username=user.username,
        question=req.question,
        question_hash=hash_question(req.question),
        model_name=usage.get("model_name", ""),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        cost=usage.get("cost", 0.0),
        response_time_ms=usage.get("response_time_ms", 0),
        search_method=result.get("search_method", ""),
        source_count=result.get("source_count", len(result.get("sources", []))),
        cached=1 if usage.get("cached") else 0,
        success=1 if result.get("llm_available", True) else 0,
        error_msg=result.get("error_msg", ""),
    )
    db.add(usage_log)
    db.commit()

    # 累加文档引用计数
    for s in result.get("sources", []):
        doc_title = s.get("title", "")
        if doc_title:
            doc = db.query(Document).filter(Document.title == doc_title).first()
            if doc:
                doc.access_count = (doc.access_count or 0) + 1
    db.commit()

    # 更新用户长期记忆（每 5 轮总结一次）
    _update_memory(user, req.question, result.get("answer", ""), db)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": result["answer"],
            "sources": [{"title": s["title"], "content": s["content"][:300], "score": s["score"]} for s in result["sources"]],
            "is_fallback": result["is_fallback"],
            "search_method": result["search_method"],
            "llm_available": result["llm_available"],
            "conversation_id": conv.conversation_id
        }
    }


@router.get("/history")
def get_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取用户对话历史"""
    records = db.query(Conversation)\
        .filter(Conversation.user_id == user.id)\
        .order_by(Conversation.created_at.desc())\
        .all()

    conversations = {}
    for r in records:
        cid = r.conversation_id
        if cid not in conversations:
            conversations[cid] = {
                "conversation_id": cid,
                "title": r.question[:30] + ("..." if len(r.question) > 30 else ""),
                "messages": [],
                "created_at": r.created_at.isoformat() if r.created_at else ""
            }
        conversations[cid]["messages"].append({"role": "user", "content": r.question})
        conversations[cid]["messages"].append({
            "role": "ai", "content": r.answer, "sources": r.sources, "feedback": r.feedback
        })

    return {"code": 0, "data": list(conversations.values())}


@router.post("/feedback")
def feedback(conversation_id: str, message_id: int, feedback_value: int,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(
        Conversation.id == message_id, Conversation.user_id == user.id
    ).first()
    if not conv:
        return {"code": 404, "message": "记录不存在"}
    conv.feedback = feedback_value
    db.commit()
    return {"code": 0, "message": "反馈已提交"}


# ==================== 用户记忆管理 ====================

def _update_memory(user, question: str, answer: str, db):
    """
    更新用户长期记忆：每对话 5 轮后生成一次摘要
    用简单的历史拼接，不调 LLM（省 Token）
    """
    # 统计该用户已有对话数
    from database import Conversation
    conv_count = db.query(Conversation).filter(
        Conversation.user_id == user.id
    ).count()

    # 每 5 轮更新一次记忆
    if conv_count % 5 == 0 and conv_count > 0:
        # 取最近 10 条对话
        recent = db.query(Conversation).filter(
            Conversation.user_id == user.id
        ).order_by(Conversation.created_at.desc()).limit(10).all()

        topics = set()
        for c in recent:
            for kw in ["奖学金", "宿舍", "选课", "社团", "军训", "考试", "毕业"]:
                if kw in (c.question or "") or kw in (c.answer or ""):
                    topics.add(kw)

        summary = "关注话题：" + "、".join(sorted(topics)) if topics else "浏览过校园知识库"
        summary += f"（共对话 {conv_count} 轮）"

        user.memory_summary = summary[:500]
        from datetime import datetime
        user.memory_updated_at = datetime.now()
        db.commit()

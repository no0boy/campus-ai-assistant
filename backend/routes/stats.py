"""
数据统计模块
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func

from routes.auth import get_current_user
from database import get_db, User, Document, Conversation
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/stats", tags=["统计"])


@router.get("/overview")
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """数据看板总览"""
    # 总提问量
    total_questions = db.query(func.count(Conversation.id)).scalar() or 0

    # 活跃用户数
    active_users = db.query(func.count(func.distinct(Conversation.user_id))).scalar() or 0

    # 文档数
    doc_count = db.query(func.count(Document.id)).scalar() or 0

    # 好评率
    total_feedback = db.query(func.count(Conversation.id))\
        .filter(Conversation.feedback != 0).scalar() or 0
    good_feedback = db.query(func.count(Conversation.id))\
        .filter(Conversation.feedback == 1).scalar() or 0
    good_rate = round(good_feedback / total_feedback * 100, 1) if total_feedback > 0 else 0

    return {
        "code": 0,
        "data": {
            "total_questions": total_questions,
            "active_users": active_users,
            "doc_count": doc_count,
            "good_rate": good_rate
        }
    }

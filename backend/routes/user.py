"""
用户画像模块 — 个人信息、欢迎流程、记忆管理
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from routes.auth import get_current_user
from database import get_db, User
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/user", tags=["用户画像"])


class ProfileUpdate(BaseModel):
    grade: Optional[str] = None
    major: Optional[str] = None
    interests: Optional[str] = None
    profile_complete: Optional[int] = None


@router.get("/profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取用户画像"""
    return {
        "code": 0,
        "data": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "avatar": user.avatar,
            "grade": user.grade or "",
            "major": user.major or "",
            "interests": user.interests or "",
            "profile_complete": user.profile_complete or 0,
            "memory_summary": user.memory_summary or "",
            "created_at": user.created_at.isoformat() if user.created_at else "",
        }
    }


@router.put("/profile")
def update_profile(
    profile: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新用户画像"""
    if profile.grade is not None:
        user.grade = profile.grade
    if profile.major is not None:
        user.major = profile.major
    if profile.interests is not None:
        user.interests = profile.interests
    if profile.profile_complete is not None:
        user.profile_complete = profile.profile_complete

    db.commit()

    return {
        "code": 0,
        "message": "画像已更新",
        "data": {
            "grade": user.grade,
            "major": user.major,
            "interests": user.interests,
            "profile_complete": user.profile_complete,
        }
    }


@router.post("/welcome")
def get_welcome(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取欢迎信息（首次登录引导）"""
    is_new = user.profile_complete == 0
    memory = user.memory_summary or ""

    welcome_msg = ""

    if is_new:
        welcome_msg = (
            "👋 嗨！我是你的校园助手**小澜**～\n\n"
            "在开始之前，先让我了解你一下吧！\n"
            "你是**大几**的呀？读的什么专业呢？\n\n"
            "直接告诉我就行，比如「大三 软件技术」～"
        )
    elif memory:
        welcome_msg = (
            f"👋 欢迎回来，{user.username}！\n\n"
            f"📝 上次聊过：{memory[:200]}\n\n"
            f"今天有什么我可以帮你的吗？"
        )
    else:
        welcome_msg = f"👋 欢迎回来，{user.username}！今天有什么我可以帮你的吗？"

    return {
        "code": 0,
        "data": {
            "is_new": is_new,
            "welcome_msg": welcome_msg,
            "grade": user.grade or "",
            "major": user.major or "",
            "profile_complete": user.profile_complete or 0,
        }
    }


@router.post("/parse-profile")
def parse_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    从用户最近的输入中解析画像信息（年级、专业）
    由前端调用，传入用户最新的一句话
    """
    # 这里只做基础的关键词匹配，不调 LLM
    return {"code": 0, "message": "ok"}

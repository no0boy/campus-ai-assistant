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


@router.get("/recommendations")
def get_recommendations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    个性化推荐：根据年级+专业+关注话题生成预测问题
    """
    grade = user.grade or ""
    major = user.major or ""
    memory = user.memory_summary or ""

    # ====== 基于年级的推荐 ======
    grade_questions = {
        "大一": ["军训时间安排", "宿舍管理规定", "选课流程是什么", "社团怎么加入", "校园一卡通怎么办理"],
        "大二": ["奖学金怎么申请", "四级考试报名时间", "转专业需要什么条件", "勤工俭学怎么申请", "选修课推荐"],
        "大三": ["实习机会怎么找", "毕业论文提交时间", "校园招聘在哪里", "考研自习室安排", "专业证书怎么考"],
        "大四": ["毕业设计答辩时间", "论文格式要求", "三方协议怎么签", "档案转寄流程", "校友卡怎么办"],
    }
    default_questions = ["宿舍几点关门", "奖学金怎么申请", "图书馆开放时间", "选课流程", "考试安排"]

    # ====== 基于专业的推荐 ======
    major_questions = {
        "软件": ["蓝桥杯竞赛报名", "ACM校队怎么进", "毕业设计选题指南", "Java/Python证书怎么考"],
        "计算机": ["蓝桥杯竞赛报名", "ACM校队怎么进", "服务器实验室怎么申请", "云计算证书怎么考"],
        "会计": ["初级会计证怎么考", "CPA报名条件", "会计师事务所实习"],
        "英语": ["专四专八报名时间", "翻译资格证书", "英语竞赛通知"],
    }

    # ====== 基于记忆的追加推荐 ======
    memory_questions = []
    if "奖学金" in memory:
        memory_questions.append("国家励志奖学金申请条件")
        memory_questions.append("奖学金到账时间")
    if "宿舍" in memory:
        memory_questions.append("宿舍调换流程")
    if "选课" in memory or "课程" in memory:
        memory_questions.append("重修政策")
        memory_questions.append("缓考怎么申请")

    # ====== 组装推荐列表 ======
    recommended = []
    seen = set()

    # 1. 年级相关
    grade_qs = grade_questions.get(grade, default_questions)
    for q in grade_qs:
        if q not in seen and len(recommended) < 6:
            recommended.append({"question": q, "source": "年级推荐"})
            seen.add(q)

    # 2. 专业相关
    for k, qs in major_questions.items():
        if k in (major or ""):
            for q in qs:
                if q not in seen and len(recommended) < 6:
                    recommended.append({"question": q, "source": "专业推荐"})
                    seen.add(q)

    # 3. 记忆相关
    for q in memory_questions:
        if q not in seen and len(recommended) < 6:
            recommended.append({"question": q, "source": "你可能关心"})
            seen.add(q)

    return {"code": 0, "data": recommended}

"""
模型配置模块 — 管理员可动态切换模型和参数
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
import json
import os

from routes.auth import get_current_user
from database import get_db, User
from services.rag_service import reload_llm
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/settings", tags=["配置"])

SETTINGS_FILE = "./model_settings.json"


class ModelSettings(BaseModel):
    model_provider: str = "qwen"
    model_name: str = "qwen3.6-plus"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048
    embedding_model: str = "text-embedding-v3"
    system_prompt: str = ""


@router.get("/model")
def get_settings(user: User = Depends(get_current_user)):
    """获取当前模型配置"""
    if not os.path.exists(SETTINGS_FILE):
        return {"code": 0, "data": ModelSettings().dict()}

    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 不返回完整 API Key，只返回前后几位
    if data.get("api_key"):
        key = data["api_key"]
        data["api_key"] = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"

    return {"code": 0, "data": data}


@router.put("/model")
def update_settings(settings: ModelSettings, user: User = Depends(get_current_user)):
    """更新模型配置（管理员专属）"""
    if user.role != "admin":
        return {"code": 403, "message": "仅管理员可修改模型配置"}

    data = settings.dict()

    # 保存到文件
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 重载大模型实例
    reload_llm(data)

    return {"code": 0, "message": "配置已保存并生效"}

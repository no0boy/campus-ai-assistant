"""
认证模块 — 登录 / 注册
JWT token 签发与验证
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from jose import jwt, JWTError
from datetime import datetime, timedelta
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from database import get_db, User, hash_password, verify_password
from sqlalchemy.orm import Session
import config

router = APIRouter(prefix="/api/auth", tags=["认证"])
security = HTTPBearer()


# ========== 请求/响应模型 ==========
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    token: str
    user: dict


# ========== 工具函数 ==========
def create_token(user_id: int, username: str, role: str) -> str:
    """签发 JWT token"""
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=config.TOKEN_EXPIRE_HOURS)
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=config.ALGORITHM)

def verify_token(token: str) -> dict:
    """验证 JWT token，返回 payload"""
    try:
        return jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="token 无效或已过期")


# ========== 获取当前用户（依赖注入） ==========
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """从请求头提取 token 并返回当前用户"""
    payload = verify_token(credentials.credentials)
    user_id = int(payload.get("sub"))
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


# ========== 接口 ==========
@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """用户登录（学生/管理员通用）"""
    user = db.query(User).filter(User.username == req.username).first()

    if not user or not verify_password(req.password, user.password):
        return {"code": 401, "message": "用户名或密码错误", "data": None}

    token = create_token(user.id, user.username, user.role)

    return {
        "code": 0,
        "message": "登录成功",
        "data": {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "avatar": user.avatar
            }
        }
    }


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """学生注册"""
    # 检查用户名是否已存在
    exist = db.query(User).filter(User.username == req.username).first()
    if exist:
        return {"code": 400, "message": "用户名已存在", "data": None}

    # 创建用户
    user = User(
        username=req.username,
        password=hash_password(req.password),
        role="student"
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token(user.id, user.username, "student")

    return {
        "code": 0,
        "message": "注册成功",
        "data": {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": "student",
                "avatar": ""
            }
        }
    }

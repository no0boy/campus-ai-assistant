"""
RBAC 权限中间件 — 基于角色的访问控制
"""

from fastapi import Request, HTTPException
from database import SessionLocal, RbacRole, User

# 路径→权限映射
PATH_PERMISSION = {
    "/api/admin": "admin",
    "/api/documents": "admin",
    "/api/stats": "admin",
    "/api/settings": "admin",
    "/api/usage": "admin",
    "/api/webhook": "api",
}

ROLE_CACHE: dict[int, dict] = {}  # 简单内存缓存角色权限


def check_permission(request: Request, user_id: int, path: str) -> bool:
    """检查用户是否有权访问该路径"""
    # 确定需要的权限
    required = None
    for prefix, perm in PATH_PERMISSION.items():
        if path.startswith(prefix):
            required = perm
            break

    if not required:
        return True  # 不需要特殊权限

    # 查角色权限
    if user_id not in ROLE_CACHE:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user and user.rbac_role_id:
                role = db.query(RbacRole).filter(RbacRole.id == user.rbac_role_id).first()
                ROLE_CACHE[user_id] = role.permissions if role else {}
            else:
                # 兼容旧数据：按 role 字段推断
                if user and user.role == "admin":
                    ROLE_CACHE[user_id] = {"chat": True, "admin": True, "api": True, "webhook": True}
                else:
                    ROLE_CACHE[user_id] = {"chat": True, "admin": False, "api": False, "webhook": False}
        finally:
            db.close()

    perms = ROLE_CACHE.get(user_id, {})
    return perms.get(required, False)


def clear_rbac_cache():
    """清空权限缓存（角色变更时调用）"""
    ROLE_CACHE.clear()

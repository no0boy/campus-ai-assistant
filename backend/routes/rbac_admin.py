"""RBAC 权限管理 API（管理员专用）"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from routes.auth import get_current_user
from database import get_db, User, RbacRole
from middleware.rbac import clear_rbac_cache
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/admin/rbac", tags=["RBAC管理"])


@router.get("/roles")
def list_roles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """查看所有角色"""
    if user.role != "admin":
        return {"code": 403, "message": "仅管理员"}
    roles = db.query(RbacRole).all()
    return {"code": 0, "data": [{"id": r.id, "name": r.name, "permissions": r.permissions} for r in roles]}


class AssignRoleReq(BaseModel):
    user_id: int
    role_name: str


@router.post("/assign")
def assign_role(req: AssignRoleReq, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """给用户分配角色"""
    if user.role != "admin":
        return {"code": 403, "message": "仅管理员"}
    role = db.query(RbacRole).filter(RbacRole.name == req.role_name).first()
    if not role:
        return {"code": 404, "message": "角色不存在"}
    target = db.query(User).filter(User.id == req.user_id).first()
    if not target:
        return {"code": 404, "message": "用户不存在"}
    target.rbac_role_id = role.id
    target.role = req.role_name
    db.commit()
    clear_rbac_cache()
    return {"code": 0, "message": f"已分配 {req.role_name} 给用户 {target.username}"}


@router.get("/my-permissions")
def my_permissions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """查看自己的权限"""
    perms = {"chat": True}
    if user.rbac_role_id:
        role = db.query(RbacRole).filter(RbacRole.id == user.rbac_role_id).first()
        if role:
            perms = role.permissions
    return {"code": 0, "data": {"username": user.username, "role": user.role, "permissions": perms}}

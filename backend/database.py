"""
数据库初始化 — SQLite + SQLAlchemy
三张表：users、documents、conversations
密码使用 SHA256 哈希（演示项目，生产环境建议用 bcrypt）
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import hashlib
import config

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ========== 密码工具 ==========
def hash_password(password: str) -> str:
    """SHA256 哈希密码"""
    return hashlib.sha256(f"{config.SECRET_KEY}{password}".encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return hash_password(plain) == hashed


# ========== 用户表 ==========
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password = Column(String(256), nullable=False)
    role = Column(String(16), default="student")
    rbac_role_id = Column(Integer, default=0)  # 关联 rbac_roles
    avatar = Column(String(256), default="")
    # 用户画像
    grade = Column(String(16), default="")          # 大一/大二/大三/大四/研究生
    major = Column(String(64), default="")          # 专业名称
    interests = Column(String(256), default="")     # 兴趣标签（逗号分隔）
    profile_complete = Column(Integer, default=0)   # 0=未完善 1=已完善
    memory_summary = Column(Text, default="")       # 历史对话摘要（长期记忆）
    memory_updated_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)


# ========== 知识库文档表 ==========
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(16), nullable=False)
    chunk_count = Column(Integer, default=0)
    access_count = Column(Integer, default=0)      # 被 RAG 检索命中的总次数
    uploader_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


# ========== 对话记录表 ==========
class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    conversation_id = Column(String(64), nullable=False, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    sources = Column(JSON, default=[])
    feedback = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


# ========== RBAC 角色权限表 ==========
class RbacRole(Base):
    __tablename__ = "rbac_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), unique=True, nullable=False)
    permissions = Column(JSON, default={})  # {"chat":true,"admin":false,"api":false}


# ========== 审计日志表 ==========
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, default=0, index=True)
    username = Column(String(64), default="")
    action = Column(String(64), default="")          # chat/login/upload/webhook
    ip_address = Column(String(45), default="")
    user_agent = Column(String(256), default="")
    request_id = Column(String(16), default="")
    detail = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.now)


# ========== Token 消耗日志表 ==========
class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    username = Column(String(64), default="")
    question = Column(Text, default="")
    question_hash = Column(String(64), default="", index=True)
    model_name = Column(String(64), default="")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    response_time_ms = Column(Integer, default=0)
    search_method = Column(String(32), default="")
    source_count = Column(Integer, default=0)
    cached = Column(Integer, default=0)
    success = Column(Integer, default=1)
    error_msg = Column(String(512), default="")
    ip_address = Column(String(45), default="")
    user_agent = Column(String(256), default="")
    request_id = Column(String(16), default="")
    created_at = Column(DateTime, default=datetime.now)


# ========== 初始化 ==========
def init_db():
    """初始化数据库，创建所有表 + 默认账号"""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 初始化默认角色
        if not db.query(RbacRole).first():
            roles = [
                RbacRole(name="admin", permissions={"chat":True,"admin":True,"api":True,"webhook":True}),
                RbacRole(name="teacher", permissions={"chat":True,"admin":True,"api":True,"webhook":False}),
                RbacRole(name="student", permissions={"chat":True,"admin":False,"api":False,"webhook":False}),
                RbacRole(name="guest", permissions={"chat":True,"admin":False,"api":False,"webhook":False}),
            ]
            db.add_all(roles)
            db.commit()
            print("[OK] 默认角色已创建: admin/teacher/student/guest")

        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                password=hash_password("admin123"),
                role="admin"
            )
            db.add(admin)
            db.commit()
            print("[OK] 默认管理员已创建: admin / admin123")

        student = db.query(User).filter(User.username == "student").first()
        if not student:
            student = User(
                username="student",
                password=hash_password("123456"),
                role="student"
            )
            db.add(student)
            db.commit()
            print("[OK] 测试学生已创建: student / 123456")

    finally:
        db.close()


def get_db():
    """获取数据库会话（FastAPI 依赖注入用）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    avatar = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.now)


# ========== 知识库文档表 ==========
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(16), nullable=False)
    chunk_count = Column(Integer, default=0)
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
    created_at = Column(DateTime, default=datetime.now)


# ========== 初始化 ==========
def init_db():
    """初始化数据库，创建所有表 + 默认账号"""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
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

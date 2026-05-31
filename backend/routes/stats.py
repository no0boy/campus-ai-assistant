"""
数据统计模块 — 运营看板 + 概览
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from datetime import datetime, timedelta

from routes.auth import get_current_user
from database import get_db, User, Document, Conversation, UsageLog
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/stats", tags=["统计"])


@router.get("/overview")
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """数据看板总览（兼容旧版）"""
    total_questions = db.query(func.count(Conversation.id)).scalar() or 0
    active_users = db.query(func.count(func.distinct(Conversation.user_id))).scalar() or 0
    doc_count = db.query(func.count(Document.id)).scalar() or 0

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


# ==================== 运营分析看板（聚合接口） ====================

@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    运营分析看板 — 一次返回所有图表数据
    包含：概览、模型分析、成本分析、RAG效果、性能分析
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 7

    # ====== 概览统计 ======
    total_questions = db.query(func.count(Conversation.id)).scalar() or 0
    today_questions = db.query(func.count(Conversation.id))\
        .filter(Conversation.created_at >= today_start).scalar() or 0
    active_users = db.query(func.count(func.distinct(UsageLog.user_id)))\
        .filter(UsageLog.created_at >= today_start - timedelta(days=30)).scalar() or 0

    total_feedback = db.query(func.count(Conversation.id))\
        .filter(Conversation.feedback != 0).scalar() or 0
    good_feedback = db.query(func.count(Conversation.id))\
        .filter(Conversation.feedback == 1).scalar() or 0
    satisfaction_rate = round(good_feedback / total_feedback * 100, 1) if total_feedback > 0 else 0

    total_tokens_all = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0)).scalar()
    today_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .filter(UsageLog.created_at >= today_start).scalar()

    total_calls = db.query(func.count(UsageLog.id)).scalar() or 0
    cached_calls = db.query(func.count(UsageLog.id)).filter(UsageLog.cached == 1).scalar() or 0
    cache_hit_rate = round(cached_calls / total_calls * 100, 1) if total_calls > 0 else 0

    avg_response = db.query(func.coalesce(func.avg(UsageLog.response_time_ms), 0))\
        .filter(UsageLog.response_time_ms > 0).scalar()

    overview = {
        "today_questions": today_questions,
        "total_questions": total_questions,
        "active_users": active_users,
        "satisfaction_rate": satisfaction_rate,
        "today_tokens": int(today_tokens),
        "total_tokens": int(total_tokens_all),
        "avg_response_ms": int(avg_response),
        "cache_hit_rate": cache_hit_rate,
    }

    # ====== 模型分析 ======
    model_rows = db.query(
        UsageLog.model_name,
        func.count(UsageLog.id).label("calls"),
        func.coalesce(func.avg(UsageLog.response_time_ms), 0).label("avg_time"),
        func.coalesce(func.avg(UsageLog.total_tokens), 0).label("avg_tokens"),
    ).filter(UsageLog.model_name != "").group_by(UsageLog.model_name).order_by(
        func.count(UsageLog.id).desc()
    ).all()

    models = []
    for row in model_rows:
        ratio = round(row.calls / total_calls * 100, 1) if total_calls > 0 else 0
        models.append({
            "name": row.model_name,
            "calls": row.calls,
            "ratio": ratio,
            "avg_time_ms": int(row.avg_time),
            "avg_tokens": int(row.avg_tokens),
        })

    # ====== 成本分析 ======
    total_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0.0)).scalar()
    today_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0.0))\
        .filter(UsageLog.created_at >= today_start).scalar()
    avg_cost = round(float(total_cost) / total_calls, 6) if total_calls > 0 else 0

    # Token 趋势
    start_date = today_start - timedelta(days=days - 1)
    token_trend_rows = db.query(
        func.date(UsageLog.created_at).label("day"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0.0).label("cost"),
        func.count(UsageLog.id).label("calls"),
    ).filter(UsageLog.created_at >= start_date).group_by(
        func.date(UsageLog.created_at)
    ).order_by(func.date(UsageLog.created_at)).all()

    token_trend = []
    for row in token_trend_rows:
        token_trend.append({
            "day": str(row.day),
            "tokens": int(row.tokens),
            "cost": round(float(row.cost), 4),
            "calls": row.calls,
        })

    cost_data = {
        "today": round(float(today_cost), 4),
        "total": round(float(total_cost), 4),
        "avg_per_question": avg_cost,
        "trend": token_trend,
    }

    # ====== RAG 效果分析 ======
    rag_total = db.query(func.count(UsageLog.id)).filter(
        UsageLog.search_method != ""
    ).scalar() or 1  # 避免除零

    vector_count = db.query(func.count(UsageLog.id)).filter(
        UsageLog.search_method == "vector"
    ).scalar() or 0
    keyword_count = db.query(func.count(UsageLog.id)).filter(
        UsageLog.search_method == "keyword"
    ).scalar() or 0

    hit_count = db.query(func.count(UsageLog.id)).filter(
        UsageLog.source_count > 0
    ).scalar() or 0
    fallback_count = db.query(func.count(UsageLog.id)).filter(
        UsageLog.source_count == 0
    ).scalar() or 0

    avg_sources = round(db.query(func.coalesce(func.avg(UsageLog.source_count), 0)).scalar(), 1)

    rag = {
        "hit_rate": round(hit_count / (hit_count + fallback_count) * 100, 1) if (hit_count + fallback_count) > 0 else 0,
        "fallback_rate": round(fallback_count / (hit_count + fallback_count) * 100, 1) if (hit_count + fallback_count) > 0 else 0,
        "vector_rate": round(vector_count / rag_total * 100, 1),
        "keyword_rate": round(keyword_count / rag_total * 100, 1),
        "avg_sources": avg_sources,
        "total_rag_calls": rag_total,
    }

    # 热门问题（按 question_hash 聚合，取原文）
    hot_rows = db.query(
        func.substr(UsageLog.question_hash, 1, 16).label("qh"),
        UsageLog.question,
        func.count(UsageLog.id).label("cnt"),
        func.coalesce(func.avg(UsageLog.total_tokens), 0).label("avg_tok"),
    ).filter(
        UsageLog.question_hash != "",
        UsageLog.question != "",
        UsageLog.success == 1,
    ).group_by(
        func.substr(UsageLog.question_hash, 1, 16)
    ).order_by(
        func.count(UsageLog.id).desc()
    ).limit(10).all()

    hot_questions = []
    for row in hot_rows:
        q = row.question or ""
        hot_questions.append({
            "question": q[:50] + ("..." if len(q) > 50 else ""),
            "count": row.cnt,
            "avg_tokens": int(row.avg_tok),
        })

    # ====== 性能分析 ======
    # P50 / P95 / P99（SQLite 用子查询模拟）
    perf_rows = db.execute(text("""
        SELECT
            COUNT(*) AS total,
            COALESCE(AVG(response_time_ms), 0) AS avg_ms
        FROM usage_logs
        WHERE response_time_ms > 0
    """)).fetchone()

    perf_total = perf_rows[0] if perf_rows else 0
    perf_avg = int(perf_rows[1]) if perf_rows else 0

    # P50 / P95 / P99 通过 SQLite OFFSET 近似
    def _percentile(pct: float) -> int:
        if perf_total == 0:
            return 0
        offset_val = max(0, int(perf_total * pct / 100) - 1)
        row = db.execute(text(
            "SELECT COALESCE(response_time_ms, 0) FROM usage_logs "
            "WHERE response_time_ms > 0 ORDER BY response_time_ms ASC "
            "LIMIT 1 OFFSET :offset"
        ), {"offset": offset_val}).fetchone()
        return int(row[0]) if row and row[0] else 0

    # 响应时间趋势
    resp_trend_rows = db.query(
        func.date(UsageLog.created_at).label("day"),
        func.coalesce(func.avg(UsageLog.response_time_ms), 0).label("avg_ms"),
        func.count(UsageLog.id).label("calls"),
    ).filter(
        UsageLog.created_at >= start_date,
        UsageLog.response_time_ms > 0,
    ).group_by(func.date(UsageLog.created_at)).order_by(func.date(UsageLog.created_at)).all()

    resp_trend = []
    for row in resp_trend_rows:
        resp_trend.append({
            "day": str(row.day),
            "avg_ms": int(row.avg_ms),
            "calls": row.calls,
        })

    performance = {
        "avg_ms": perf_avg,
        "p50_ms": _percentile(50),
        "p95_ms": _percentile(95),
        "p99_ms": _percentile(99),
        "trend": resp_trend,
    }

    # ====== 组装返回 ======
    return {
        "code": 0,
        "data": {
            "overview": overview,
            "models": models,
            "cost": cost_data,
            "rag": rag,
            "hot_questions": hot_questions,
            "performance": performance,
        }
    }

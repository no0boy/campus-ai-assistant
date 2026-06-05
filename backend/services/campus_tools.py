"""
校园工具集 — Tool Calling 模拟数据（可替换为真实 API）
"""

import random
from datetime import datetime, timedelta

# ====== 模拟数据 ======

_COURSES = {
    "周一": [("08:00-09:40", "数据结构", "教学楼A301", "张教授"),
             ("10:00-11:40", "操作系统", "实验楼B202", "李老师"),
             ("14:00-15:40", "软件工程", "教学楼C105", "王教授")],
    "周二": [("08:00-09:40", "计算机网络", "教学楼A201", "陈老师"),
             ("10:00-11:40", "数据库原理", "实验楼B101", "刘教授")],
    "周三": [("08:00-09:40", "数据结构(实验)", "实验楼B202", "张教授"),
             ("14:00-15:40", "操作系统(实验)", "实验楼B101", "李老师")],
    "周四": [("10:00-11:40", "大学英语", "外语楼301", "外教"),
             ("14:00-15:40", "思想政治", "教学楼A101", "赵老师")],
    "周五": [("08:00-09:40", "体育", "体育馆", "周教练"),
             ("10:00-11:40", "软件工程(实验)", "实验楼C201", "王教授")],
}

_GRADES = {
    "数据结构": 92, "操作系统": 88, "软件工程": 95, "计算机网络": 85,
    "数据库原理": 90, "大学英语": 87, "思想政治": 91, "体育": 93,
}

_NOTICES = [
    ("2025-09-01", "【教务处】2025-2026学年第一学期选课通知：请于9月5日前登录教务系统完成选课"),
    ("2025-09-10", "【学生处】关于开展2025年国家奖学金评选工作的通知"),
    ("2025-09-15", "【团委】关于举办校园文化节的通知"),
    ("2025-10-08", "【教务处】期中考试安排已发布，请查看教务系统"),
    ("2025-11-01", "【就业办】2025届毕业生校园招聘会将于11月15日在体育馆举行"),
]

_CALENDAR = {
    "开学日期": "2025年9月1日",
    "军训时间": "大一新生9月1日-9月14日",
    "期中考试": "第10周（11月3日-11月7日）",
    "期末考试": "第18-19周（12月29日-1月9日）",
    "寒假开始": "2026年1月12日",
    "运动会": "10月20日-10月21日",
}


def get_course(user_input: str = "") -> str:
    """查询课表"""
    today = datetime.now()
    weekday_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
    if today.weekday() >= 5:
        return "今天是周末，没有课程安排。要不要查一下周一的课表？"

    courses = _COURSES.get(weekday_name, [])
    if not courses:
        return f"{weekday_name}暂无课程安排"

    lines = [f"📅 {weekday_name}课表："]
    for t, name, loc, teacher in courses:
        lines.append(f"  {t} | {name} | {loc} | {teacher}")
    return "\n".join(lines)


def get_grade(user_input: str = "") -> str:
    """查询成绩"""
    lines = ["📊 本学期成绩："]
    total = 0
    for course, score in _GRADES.items():
        grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 60 else "D"
        lines.append(f"  {course}: {score}分 ({grade})")
        total += score
    avg = total / len(_GRADES)
    lines.append(f"  平均分: {avg:.1f}")
    return "\n".join(lines)


def get_notice(user_input: str = "") -> str:
    """查询最新通知"""
    lines = ["📢 最新通知（前5条）："]
    for date, text in _NOTICES[-5:]:
        lines.append(f"  [{date}] {text[:60]}...")
    return "\n".join(lines)


def get_calendar(user_input: str = "") -> str:
    """查询校历"""
    lines = ["🗓️ 校历："]
    for k, v in _CALENDAR.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


# ====== 工具注册表 ======

CAMPUS_TOOLS = {
    "查课表":   {"fn": get_course,   "desc": "查询今日或指定日期的课程表"},
    "查成绩":   {"fn": get_grade,    "desc": "查询本学期各科成绩及平均分"},
    "查通知":   {"fn": get_notice,   "desc": "查询学校最新通知公告"},
    "查校历":   {"fn": get_calendar, "desc": "查询学期安排、考试日期、假期"},
}

"""
工作流引擎 — 可配置的 Agent 行为编排
管理员预设工作流模板，Agent 按配置执行
"""

# ====== 工作流模板 ======

WORKFLOWS = {
    "奖学金咨询": {
        "trigger": ["奖学金", "助学金", "励志", "贫困补助"],
        "steps": [
            {"type": "tool", "tool": "查课表", "desc": "了解学生课业负担"},
            {"type": "search", "keyword": "奖学金 申请条件 金额"},
            {"type": "answer", "prompt": "根据学生画像和检索结果，个性化回复奖学金申请建议"},
        ]
    },
    "宿舍咨询": {
        "trigger": ["宿舍", "关门", "熄灯", "热水", "空调", "电器"],
        "steps": [
            {"type": "search", "keyword": "宿舍管理 规定"},
            {"type": "answer", "prompt": "根据学生手册回答宿舍相关问题"},
        ]
    },
    "教务咨询": {
        "trigger": ["课表", "成绩", "考试", "放假", "校历", "通知"],
        "steps": [
            {"type": "tool", "tool": "auto", "desc": "根据问题自动选择查课表/查成绩/查通知/查校历"},
            {"type": "answer", "prompt": "结合工具返回的教务数据回答"},
        ]
    },
    "综合问答": {
        "trigger": [],
        "steps": [
            {"type": "search", "keyword": "auto"},
            {"type": "answer", "prompt": "综合回答"},
        ]
    },
}


def match_workflow(question: str) -> dict:
    """根据问题匹配最合适的工作流"""
    best = WORKFLOWS.get("综合问答", {})
    best_score = 0
    for name, wf in WORKFLOWS.items():
        if name == "综合问答":
            continue
        score = sum(1 for kw in wf.get("trigger", []) if kw in question)
        if score > best_score:
            best_score = score
            best = wf
    return best


def execute_workflow(question: str, user_profile: dict = None) -> str:
    """
    执行工作流
    返回每一步的执行上下文（用于 Agent Think-Act）
    """
    wf = match_workflow(question)
    context_lines = [f"工作流: {wf.get('trigger', [])[0] if wf.get('trigger') else '综合'}模式"]

    for i, step in enumerate(wf.get("steps", [])):
        stype = step.get("type", "")
        if stype == "search":
            kw = step.get("keyword", question)
            if kw == "auto":
                kw = question
            context_lines.append(f"Step{i + 1}: 建议检索「{kw}」")
        elif stype == "tool":
            tool_name = step.get("tool", "")
            if tool_name == "auto":
                # 自动匹配工具
                for name in ["查课表", "查成绩", "查通知", "查校历"]:
                    if any(kw in question for kw in ["课表", "课程", "上课"]):
                        tool_name = "查课表"; break
                    elif any(kw in question for kw in ["成绩", "分数", "绩点"]):
                        tool_name = "查成绩"; break
                    elif any(kw in question for kw in ["通知", "公告"]):
                        tool_name = "查通知"; break
                    elif any(kw in question for kw in ["放假", "校历", "开学", "考试时间"]):
                        tool_name = "查校历"; break
            context_lines.append(f"Step{i + 1}: 建议调用工具「{tool_name}」")
        elif stype == "answer":
            context_lines.append(f"Step{i + 1}: {step.get('prompt', '生成回答')}")

    return "\n".join(context_lines)

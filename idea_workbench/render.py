from __future__ import annotations

from datetime import datetime
from typing import Any


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    escaped_headers = [escape_cell(header) for header in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_cell(value) for value in row) + " |")
    return "\n".join(lines)


def escape_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\n", "<br>")
    return text.replace("|", "\\|")


def render_decomposition(data: dict[str, Any]) -> str:
    lines = [
        "# Idea 拆解报告",
        "",
        f"生成时间：{timestamp()}",
        "",
        "## 主题",
        "",
        data.get("topic", "未命名科研想法"),
        "",
        "## 关键词",
        "",
        ", ".join(data.get("terms", [])) or "暂无",
        "",
        "## 可检索 Claims",
        "",
    ]
    rows = []
    for claim in data.get("claims", []):
        rows.append(
            [
                claim.get("id", ""),
                claim.get("type", ""),
                claim.get("claim", ""),
                claim.get("why_it_matters", ""),
                claim.get("risk_if_false", ""),
            ]
        )
    lines.append(md_table(["ID", "类型", "Claim", "为什么重要", "若不成立的风险"], rows))
    lines.extend(["", "## 需要优先回答的问题", ""])
    for item in data.get("risk_questions", []):
        lines.append(f"- {item}")
    lines.extend(["", "## 下一步", "", data.get("suggested_next_step", "")])
    return "\n".join(lines).strip() + "\n"


def render_queries(queries: list[dict[str, str]]) -> str:
    lines = [
        "# Generated search queries",
        "# Edit this file if you want to add or rewrite queries.",
        "",
    ]
    for query in queries:
        lines.extend(
            [
                f"- id: {query['id']}",
                f"  claim_id: {query['claim_id']}",
                f"  intent: {query['intent']}",
                f"  query: {query['query']}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_search_log(queries: list[dict[str, str]], papers: list[dict[str, Any]], errors: list[str]) -> str:
    lines = [
        "# 文献检索记录",
        "",
        f"生成时间：{timestamp()}",
        "",
        f"- Query 数量：{len(queries)}",
        f"- 论文数量：{len(papers)}",
        f"- 错误/提示数量：{len(errors)}",
        "",
        "## Queries",
        "",
    ]
    lines.append(md_table(["ID", "Claim", "意图", "Query"], [[q["id"], q["claim_id"], q["intent"], q["query"]] for q in queries]))
    lines.extend(["", "## Papers", ""])
    if papers:
        lines.append(md_table(["标题", "年份", "来源", "URL"], [[p.get("title", ""), p.get("year", ""), p.get("source", ""), p.get("url", "")] for p in papers[:80]]))
    else:
        lines.append("暂无 API 检索结果。可以先人工根据 `queries.yaml` 检索，并把论文元数据填入 `papers/manual_papers.json`。")
    if errors:
        lines.extend(["", "## Errors / Notes", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).strip() + "\n"


def render_matrix(matrix: dict[str, Any]) -> str:
    lines = [
        "# Novelty Matrix",
        "",
        f"生成时间：{timestamp()}",
        "",
        f"> {matrix.get('warning', '')}",
        "",
        "## Claim 风险总览",
        "",
    ]
    rows = []
    for row in matrix.get("rows", []):
        closest = "<br>".join(
            f"{paper.get('score', 0)} - {paper.get('title', '')} ({paper.get('year', '')}, {paper.get('source', '')})"
            for paper in row.get("closest_papers", [])
        )
        rows.append(
            [
                row.get("claim_id", ""),
                row.get("risk", ""),
                row.get("max_overlap", ""),
                row.get("evidence_count", ""),
                row.get("claim", ""),
                closest or "暂无证据",
            ]
        )
    lines.append(md_table(["Claim", "风险", "最高重合", "证据数", "内容", "最接近论文"], rows))
    lines.extend(["", "## 解释", ""])
    lines.append("- 高/中/低风险来自关键词和摘要的初筛重合度，只能作为下一步人工阅读优先级。")
    lines.append("- `未知` 通常表示还没有检索证据，不表示新颖。")
    lines.append("- 真正的 related work 判断需要人工阅读最接近论文。")
    return "\n".join(lines).strip() + "\n"


def render_refined_ideas(ideas: list[dict[str, Any]]) -> str:
    lines = ["# 打磨后的候选 Idea", "", f"生成时间：{timestamp()}", ""]
    for index, idea in enumerate(ideas, start=1):
        lines.extend(
            [
                f"## {index}. {idea['name']}",
                "",
                f"- 研究问题：{idea['research_question']}",
                f"- 技术动作：{idea['technical_move']}",
                f"- 新颖性杠杆：{idea['novelty_lever']}",
                f"- 主要风险：{idea['main_risk']}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_experiment_plan(plan: dict[str, Any]) -> str:
    lines = [
        "# 最小实验计划",
        "",
        f"生成时间：{timestamp()}",
        "",
        f"目标：{plan.get('objective', '')}",
        "",
        f"查重风险提示：{plan.get('risk_note', '')}",
        "",
        "## 实验阶段",
        "",
    ]
    for phase in plan.get("phases", []):
        lines.extend(
            [
                f"### {phase['name']}",
                "",
                f"- 目标：{phase['goal']}",
                f"- 接受标准：{phase['acceptance']}",
                "",
            ]
        )
    lines.extend(["## Baselines", ""])
    for item in plan.get("baselines", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Metrics", ""])
    for item in plan.get("metrics", []):
        lines.append(f"- {item}")
    lines.extend(["", "## 失败判据", ""])
    for item in plan.get("failure_criteria", []):
        lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def render_final_report(
    decomposition_md: str,
    matrix_md: str,
    refined_md: str,
    experiment_md: str,
) -> str:
    sections = [
        "# 科研 Idea Workbench 总报告",
        "",
        f"生成时间：{timestamp()}",
        "",
        "这份报告是检索证据驱动的 idea 打磨草案，不是新颖性证明。建议把 `Novelty Matrix` 中高/中风险 claim 对应论文人工读完后再定题。",
        "",
        "---",
        "",
        decomposition_md,
        "",
        "---",
        "",
        matrix_md,
        "",
        "---",
        "",
        refined_md,
        "",
        "---",
        "",
        experiment_md,
    ]
    return "\n".join(section.strip() for section in sections if section is not None).strip() + "\n"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

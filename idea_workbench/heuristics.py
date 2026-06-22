from __future__ import annotations

import re
from collections import Counter
from typing import Any


EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "via",
    "with",
}

CN_STOPWORDS = {
    "一个",
    "一种",
    "这个",
    "那个",
    "可以",
    "可能",
    "是否",
    "如何",
    "通过",
    "因为",
    "所以",
    "但是",
    "以及",
    "进行",
    "研究",
    "方法",
    "问题",
    "原始想法",
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[*_>\[\]()]|https?://\S+", " ", text)
    return normalize_space(text)


def extract_terms(text: str, domain_keywords: list[str], *, limit: int = 18) -> list[str]:
    clean = strip_markdown(text)
    terms: list[str] = []

    lowered = clean.lower()
    for keyword in domain_keywords:
        if keyword.lower() in lowered and keyword not in terms:
            terms.append(keyword)

    # English phrases and Chinese chunks are both useful for query generation.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", clean)
    normalized: list[str] = []
    for token in tokens:
        value = token.lower() if token.isascii() else token
        if value in EN_STOPWORDS or value in CN_STOPWORDS:
            continue
        if len(value) < 2:
            continue
        normalized.append(value)

    counts = Counter(normalized)
    for token, _count in counts.most_common(limit * 2):
        if token not in terms:
            terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def short_topic(seed_text: str, terms: list[str]) -> str:
    meaningful_lines: list[str] = []
    for raw_line in seed_text.splitlines():
        line = re.sub(r"^#+\s*", "", raw_line).strip()
        line = re.sub(r"^[-*]\s*", "", line).strip()
        if not line or line in {"原始想法", "背景", "问题", "想法", "Idea", "Seed"}:
            continue
        meaningful_lines.append(line)
    clean = strip_markdown(" ".join(meaningful_lines) if meaningful_lines else seed_text)
    first_sentence = re.split(r"[。.!?\n]", clean, maxsplit=1)[0].strip()
    if 12 <= len(first_sentence) <= 120:
        return first_sentence
    if terms:
        return " / ".join(terms[:4])
    return "未命名科研想法"


def decompose_seed(seed_text: str, config: dict[str, Any]) -> dict[str, Any]:
    domain_keywords = list(config.get("domain_keywords", []))
    terms = extract_terms(seed_text, domain_keywords)
    topic = short_topic(seed_text, terms)
    domain_focus = terms[:6] if terms else domain_keywords[:6]
    topic_phrase = "、".join(domain_focus[:4]) if domain_focus else topic

    claims = [
        {
            "id": "C1",
            "type": "problem_gap",
            "claim": f"在 {topic_phrase} 相关问题中，现有方法可能还没有充分覆盖这个具体设定：{topic}",
            "why_it_matters": "这是查重的主 claim：如果已有论文已经完整覆盖该设定，idea 需要换角度。",
            "risk_if_false": "如果该问题已有成熟解决方案，贡献点会变成工程复现或小改动。",
        },
        {
            "id": "C2",
            "type": "method_hypothesis",
            "claim": f"引入 {topic_phrase} 中的关键机制，可能改善具身智能系统的建模、规划、泛化或样本效率。",
            "why_it_matters": "这是方法有效性的 claim，需要找相邻方法、替代方法和失败案例。",
            "risk_if_false": "如果相邻工作已经证明该机制无效或收益很小，需要降低主张或寻找更窄场景。",
        },
        {
            "id": "C3",
            "type": "evaluation_claim",
            "claim": "该想法的贡献需要通过任务成功率、预测误差、泛化、鲁棒性、样本效率或 ablation 差异体现，而不只是换一个模块名称。",
            "why_it_matters": "这是最小实验计划的核心：idea 必须落到可检验指标上。",
            "risk_if_false": "如果没有清晰指标，后续会很难说服审稿人这是研究贡献。",
        },
        {
            "id": "C4",
            "type": "novelty_boundary",
            "claim": "真正需要验证的新颖性边界，是当前 idea 与 world model、model-based RL、robot learning、representation learning、differentiable simulation 等邻近工作的差异。",
            "why_it_matters": "这是防止“只查一个关键词”的边界 claim。",
            "risk_if_false": "如果没有横向比较，工具可能漏掉换名但等价的已有工作。",
        },
    ]

    risk_questions = [
        "有没有论文已经在同一任务、同一模型机制、同一评价指标上做过？",
        "相邻领域是否用不同术语表达了同一个想法？",
        "idea 的收益是否只来自更大的模型、更长训练或更多数据？",
        "最小实验能否在一周内给出正/负信号？",
        "如果核心假设失败，是否还能留下有价值的负结果或诊断结论？",
    ]

    return {
        "topic": topic,
        "terms": terms,
        "claims": claims,
        "risk_questions": risk_questions,
        "suggested_next_step": "先运行 search 和 matrix，确认 C1/C2 的高重合风险，再决定是否强化差异点。",
    }


def generate_queries(decomposition: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    terms = list(decomposition.get("terms", []))
    claims = list(decomposition.get("claims", []))
    domain_keywords = list(config.get("domain_keywords", []))
    topic = decomposition.get("topic", "research idea")

    query_terms = terms[:8] or domain_keywords[:8]
    core = " ".join(query_terms[:4]) or topic
    embodied = " ".join(pick_existing(query_terms, ["embodied intelligence", "robot learning", "robot manipulation", "world model"]))
    if not embodied:
        embodied = "robot learning world model"

    queries: list[dict[str, str]] = []
    for claim in claims:
        claim_id = claim["id"]
        claim_text = claim["claim"]
        local_terms = extract_terms(claim_text, domain_keywords, limit=8)
        local_core = " ".join(local_terms[:5]) or core
        queries.extend(
            [
                {
                    "id": f"Q-{claim_id}-exact",
                    "claim_id": claim_id,
                    "intent": "精确查重",
                    "query": f'"{local_core}" {embodied}',
                },
                {
                    "id": f"Q-{claim_id}-adjacent",
                    "claim_id": claim_id,
                    "intent": "相邻领域",
                    "query": f"{local_core} model-based reinforcement learning representation learning planning",
                },
                {
                    "id": f"Q-{claim_id}-negative",
                    "claim_id": claim_id,
                    "intent": "反向/失败案例",
                    "query": f"{local_core} limitation failure ablation benchmark",
                },
            ]
        )

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for query in queries:
        key = query["query"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


def pick_existing(values: list[str], candidates: list[str]) -> list[str]:
    lowered = {value.lower(): value for value in values}
    return [lowered[candidate.lower()] for candidate in candidates if candidate.lower() in lowered]


def paper_text(paper: dict[str, Any]) -> str:
    return " ".join(
        str(paper.get(key, ""))
        for key in ("title", "abstract", "venue", "source", "year")
        if paper.get(key)
    )


def score_overlap(claim: str, paper: dict[str, Any], domain_keywords: list[str]) -> float:
    claim_terms = set(extract_terms(claim, domain_keywords, limit=16))
    paper_terms = set(extract_terms(paper_text(paper), domain_keywords, limit=28))
    if not claim_terms or not paper_terms:
        return 0.0
    overlap = claim_terms & paper_terms
    return round(len(overlap) / max(len(claim_terms), 1), 3)


def risk_label(score: float, evidence_count: int) -> str:
    if evidence_count == 0:
        return "未知"
    if score >= 0.45:
        return "高"
    if score >= 0.25:
        return "中"
    return "低"


def build_novelty_matrix(
    decomposition: dict[str, Any],
    papers: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    domain_keywords = list(config.get("domain_keywords", []))
    rows: list[dict[str, Any]] = []
    for claim in decomposition.get("claims", []):
        scored = []
        for paper in papers:
            score = score_overlap(claim["claim"], paper, domain_keywords)
            if score > 0:
                scored.append((score, paper))
        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:5]
        max_score = top[0][0] if top else 0.0
        rows.append(
            {
                "claim_id": claim["id"],
                "claim": claim["claim"],
                "risk": risk_label(max_score, len(top)),
                "max_overlap": max_score,
                "evidence_count": len(top),
                "closest_papers": [
                    {
                        "score": score,
                        "title": paper.get("title", ""),
                        "year": paper.get("year", ""),
                        "source": paper.get("source", ""),
                        "url": paper.get("url", ""),
                        "overlap_reason": infer_overlap_reason(claim["claim"], paper, domain_keywords),
                    }
                    for score, paper in top
                ],
            }
        )

    return {
        "warning": "该矩阵是检索证据驱动的初筛，不是新颖性证明。",
        "rows": rows,
    }


def infer_overlap_reason(claim: str, paper: dict[str, Any], domain_keywords: list[str]) -> str:
    claim_terms = set(extract_terms(claim, domain_keywords, limit=12))
    paper_terms = set(extract_terms(paper_text(paper), domain_keywords, limit=24))
    overlap = sorted(claim_terms & paper_terms)
    if not overlap:
        return "关键词重合较少，需要人工判断是否概念等价。"
    return "共同关键词: " + ", ".join(overlap[:8])


def refine_ideas(decomposition: dict[str, Any], matrix: dict[str, Any]) -> list[dict[str, Any]]:
    topic = decomposition.get("topic", "未命名科研想法")
    risky_claims = [row for row in matrix.get("rows", []) if row.get("risk") in {"高", "中"}]
    risk_summary = "、".join(row["claim_id"] for row in risky_claims) or "暂无明显高重合 claim"
    terms = decomposition.get("terms", [])
    anchor = "、".join(terms[:4]) if terms else topic

    return [
        {
            "name": "保守可做版",
            "research_question": f"在一个受控的具身智能任务中，{topic} 是否能稳定改善一个明确指标？",
            "technical_move": "缩小任务范围，固定 baseline 和数据预算，只验证一个核心机制。",
            "novelty_lever": "贡献重点放在清晰问题设定、可复现实验和负/正结果诊断。",
            "main_risk": f"可能与 {risk_summary} 对应的已有工作重合，需要在 related work 中精确区分。",
        },
        {
            "name": "差异强化版",
            "research_question": f"能否把 {anchor} 的差异点变成一个已有方法无法自然覆盖的设定？",
            "technical_move": "强化任务约束、交互闭环、物理结构、长时序规划或泛化条件中的一个维度。",
            "novelty_lever": "不只换模型模块，而是提出新的评价切面或失败模式。",
            "main_risk": "差异点如果只体现在表述上，审稿人仍会认为是已有方法组合。",
        },
        {
            "name": "跨领域类比版",
            "research_question": f"相邻领域中处理 {anchor} 的机制，能否迁移到 embodied/world-model 场景？",
            "technical_move": "从 representation learning、planning、differentiable simulation 或 model-based RL 中寻找结构类比。",
            "novelty_lever": "把成熟机制迁移到新的具身闭环问题，并解释为什么原领域假设在这里不完全成立。",
            "main_risk": "类比需要落到可测假设，否则会变成宽泛综述。",
        },
        {
            "name": "审稿风险最低版",
            "research_question": f"围绕 {topic}，能否产出一个小而硬的 empirical finding？",
            "technical_move": "优先做 ablation、stress test 和失败条件分析，减少大而空的算法主张。",
            "novelty_lever": "贡献定位为发现、诊断或基准，而不是宣称通用 agent 能力。",
            "main_risk": "如果实验现象不稳定，论文叙事会缺少中心发现。",
        },
    ]


def build_experiment_plan(decomposition: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    topic = decomposition.get("topic", "未命名科研想法")
    high_risk = [row["claim_id"] for row in matrix.get("rows", []) if row.get("risk") == "高"]
    risk_note = "、".join(high_risk) if high_risk else "暂无高风险 claim，但仍需人工查重"

    return {
        "objective": f"用最小实验验证：{topic}",
        "risk_note": risk_note,
        "phases": [
            {
                "name": "Phase 0: sanity check",
                "goal": "确认任务、环境、数据读取、指标计算和 baseline 都能稳定运行。",
                "acceptance": "随机策略/最弱 baseline 有合理低分，oracle 或强 baseline 有明显高分。",
            },
            {
                "name": "Phase 1: minimal positive signal",
                "goal": "只验证一个核心机制是否带来可观增益。",
                "acceptance": "在固定预算下，相对主 baseline 至少有稳定正向差异，且 3 个随机种子方向一致。",
            },
            {
                "name": "Phase 2: ablation",
                "goal": "移除 world model/WAM/CILD/representation/differentiable component 中的关键部分。",
                "acceptance": "关键模块被移除后，目标指标下降；若不下降，需要重写贡献主张。",
            },
            {
                "name": "Phase 3: stress test",
                "goal": "改变对象、动力学、初始状态、时序长度或观测噪声，检查泛化和失败边界。",
                "acceptance": "至少发现一个明确优势区间或失败模式，能支撑论文叙事。",
            },
        ],
        "baselines": [
            "nearest published method from novelty matrix",
            "model-free RL / imitation baseline",
            "model-based RL or world-model baseline",
            "same architecture without the proposed key mechanism",
            "simple heuristic or oracle upper-bound where possible",
        ],
        "metrics": [
            "task success rate",
            "sample efficiency",
            "prediction error or planning error",
            "robustness under distribution shift",
            "ablation delta",
            "runtime and data cost",
        ],
        "failure_criteria": [
            "核心模块 ablation 后没有下降",
            "只在单一 seed 或单一环境参数下有效",
            "增益主要来自更多参数、更多训练步或额外数据",
            "与最近论文的实验设定无法区分",
        ],
    }

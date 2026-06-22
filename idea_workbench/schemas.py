from __future__ import annotations

from typing import Any


class SchemaError(ValueError):
    """Raised when model output does not match the expected lightweight schema."""


BRIEF_SCHEMA = {
    "topic": "string",
    "problem_statement": "string",
    "domain": ["string"],
    "known_context": ["string"],
    "constraints": ["string"],
    "non_goals": ["string"],
    "success_criteria": ["string"],
    "uncertainties": ["string"],
}

CLAIMS_SCHEMA = {
    "claims": [
        {
            "id": "C1",
            "type": "problem_gap|method_hypothesis|evaluation_claim|novelty_boundary|risk",
            "claim": "string",
            "mechanism": "string",
            "task_context": "string",
            "why_it_matters": "string",
            "risk_if_false": "string",
            "equivalent_terms": ["string"],
            "search_priority": "high|medium|low",
        }
    ],
    "risk_questions": ["string"],
}

QUERY_SCHEMA = {
    "queries": [
        {
            "id": "Q-C1-exact",
            "claim_id": "C1",
            "intent": "exact|renaming|adjacent|negative|recent",
            "query": "string",
            "rationale": "string",
        }
    ]
}

TRIAGE_SCHEMA = {
    "papers": [
        {
            "paper_id": "string",
            "title": "string",
            "relevance": "high|medium|low|irrelevant",
            "matched_claims": ["C1"],
            "core_contribution": "string",
            "why_relevant": "string",
            "risk_signal": "string",
        }
    ]
}

NOVELTY_SCHEMA = {
    "warning": "string",
    "rows": [
        {
            "claim_id": "C1",
            "claim": "string",
            "risk": "high|medium|low|unknown",
            "closest_papers": [
                {
                    "title": "string",
                    "year": "string",
                    "url": "string",
                    "overlap": "string",
                    "difference": "string",
                    "evidence_strength": "strong|medium|weak",
                }
            ],
            "missing_evidence": ["string"],
            "positioning": "string",
        }
    ],
    "overall_recommendation": "proceed|proceed_with_caution|pivot|abandon",
}

REVIEW_SCHEMA = {
    "summary": "string",
    "score": "number 1-10",
    "recommendation": "proceed|proceed_with_caution|pivot|abandon",
    "strongest_objections": ["string"],
    "minimum_fixes": ["string"],
    "reviewer_likely_prior_work_attack": ["string"],
    "experiment_concerns": ["string"],
    "positioning_advice": "string",
}

IDEA_SCHEMA = {
    "ideas": [
        {
            "name": "string",
            "research_question": "string",
            "method": ["string"],
            "novelty_lever": "string",
            "minimum_experiment": "string",
            "main_risk": "string",
            "expected_contribution": "empirical|method|diagnostic|benchmark|theory",
            "rank": "integer",
        }
    ]
}

EXPERIMENT_SCHEMA = {
    "objective": "string",
    "phases": [
        {
            "name": "string",
            "goal": "string",
            "acceptance": "string",
        }
    ],
    "baselines": ["string"],
    "metrics": ["string"],
    "ablations": ["string"],
    "failure_criteria": ["string"],
    "results_to_claims": [
        {
            "possible_result": "string",
            "allowed_claim": "string",
            "forbidden_claim": "string",
        }
    ],
}


def ensure_dict(value: Any, schema_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{schema_name} must be a JSON object")
    return value


def require_keys(data: dict[str, Any], keys: list[str], schema_name: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise SchemaError(f"{schema_name} missing required keys: {', '.join(missing)}")


def normalize_brief(data: Any) -> dict[str, Any]:
    obj = ensure_dict(data, "ResearchBrief")
    require_keys(obj, ["topic", "problem_statement"], "ResearchBrief")
    for key in ("domain", "known_context", "constraints", "non_goals", "success_criteria", "uncertainties"):
        obj[key] = ensure_string_list(obj.get(key, []))
    return obj


def normalize_claims(data: Any) -> dict[str, Any]:
    obj = ensure_dict(data, "Claims")
    claims = obj.get("claims")
    if not isinstance(claims, list) or not claims:
        raise SchemaError("Claims.claims must be a non-empty list")
    normalized = []
    for index, claim in enumerate(claims, start=1):
        item = ensure_dict(claim, "Claim")
        item.setdefault("id", f"C{index}")
        item.setdefault("type", "method_hypothesis")
        item.setdefault("mechanism", "")
        item.setdefault("task_context", "")
        item.setdefault("why_it_matters", "")
        item.setdefault("risk_if_false", "")
        item.setdefault("equivalent_terms", [])
        item.setdefault("search_priority", "medium")
        require_keys(item, ["id", "claim"], "Claim")
        item["equivalent_terms"] = ensure_string_list(item.get("equivalent_terms", []))
        normalized.append(item)
    obj["claims"] = normalized
    obj["risk_questions"] = ensure_string_list(obj.get("risk_questions", []))
    return obj


def normalize_queries(data: Any) -> list[dict[str, str]]:
    obj = ensure_dict(data, "Queries")
    queries = obj.get("queries")
    if not isinstance(queries, list) or not queries:
        raise SchemaError("Queries.queries must be a non-empty list")
    normalized: list[dict[str, str]] = []
    for index, query in enumerate(queries, start=1):
        item = ensure_dict(query, "SearchQuery")
        require_keys(item, ["query"], "SearchQuery")
        normalized.append(
            {
                "id": str(item.get("id") or f"Q{index}"),
                "claim_id": str(item.get("claim_id") or "manual"),
                "intent": str(item.get("intent") or "llm"),
                "query": str(item["query"]),
                "rationale": str(item.get("rationale") or ""),
            }
        )
    return normalized


def normalize_matrix(data: Any) -> dict[str, Any]:
    obj = ensure_dict(data, "NoveltyMatrix")
    rows = obj.get("rows")
    if not isinstance(rows, list):
        raise SchemaError("NoveltyMatrix.rows must be a list")
    obj.setdefault("warning", "该矩阵是检索证据驱动的初筛，不是新颖性证明。")
    obj.setdefault("overall_recommendation", "proceed_with_caution")
    return obj


def normalize_review(data: Any) -> dict[str, Any]:
    obj = ensure_dict(data, "ReviewerReport")
    require_keys(obj, ["summary", "recommendation"], "ReviewerReport")
    obj.setdefault("score", 0)
    for key in ("strongest_objections", "minimum_fixes", "reviewer_likely_prior_work_attack", "experiment_concerns"):
        obj[key] = ensure_string_list(obj.get(key, []))
    obj.setdefault("positioning_advice", "")
    return obj


def normalize_ideas(data: Any) -> list[dict[str, Any]]:
    obj = ensure_dict(data, "IdeaCandidates")
    ideas = obj.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        raise SchemaError("IdeaCandidates.ideas must be a non-empty list")
    for index, idea in enumerate(ideas, start=1):
        item = ensure_dict(idea, "IdeaCandidate")
        item.setdefault("rank", index)
        item.setdefault("method", [])
        item["method"] = ensure_string_list(item.get("method", []))
    return ideas


def normalize_experiment(data: Any) -> dict[str, Any]:
    obj = ensure_dict(data, "ExperimentPlan")
    require_keys(obj, ["objective"], "ExperimentPlan")
    for key in ("phases", "baselines", "metrics", "ablations", "failure_criteria", "results_to_claims"):
        obj.setdefault(key, [])
    return obj


def ensure_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []

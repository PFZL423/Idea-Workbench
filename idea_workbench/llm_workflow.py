from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import detect_evidence_backend, run_evidence_qa
from .heuristics import build_experiment_plan, build_novelty_matrix, decompose_seed, refine_ideas
from .models import ModelConfigError, call_json, doctor as model_doctor, get_model_tier
from .project import IdeaProject, load_config, read_json, read_text, write_json, write_text
from .render import (
    md_table,
    render_decomposition,
    render_experiment_plan,
    render_final_report,
    render_matrix,
    render_queries,
    render_search_log,
)
from .schemas import (
    BRIEF_SCHEMA,
    CLAIMS_SCHEMA,
    EXPERIMENT_SCHEMA,
    IDEA_SCHEMA,
    NOVELTY_SCHEMA,
    QUERY_SCHEMA,
    REVIEW_SCHEMA,
    normalize_brief,
    normalize_claims,
    normalize_experiment,
    normalize_ideas,
    normalize_matrix,
    normalize_queries,
    normalize_review,
)
from .search import run_search
from .tracing import TraceLogger


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def doctor_report(project: IdeaProject | None = None) -> dict[str, Any]:
    config = load_config(project) if project else {}
    if not config:
        from .project import DEFAULT_CONFIG

        config = DEFAULT_CONFIG
    report = model_doctor(config)
    report["paper_search_mcp"] = detect_paper_search_mcp()
    report["evidence_qa"] = detect_evidence_backend().__dict__
    return report


def detect_paper_search_mcp() -> dict[str, Any]:
    from .search import resolve_paper_search_mcp_repo

    repo = resolve_paper_search_mcp_repo()
    return {
        "available": repo is not None,
        "path": str(repo) if repo else "",
    }


def render_doctor(report: dict[str, Any]) -> str:
    rows = [
        [
            row["tier"],
            row["model"],
            row["reasoning_effort"],
            row["base_url_env"],
            "yes" if row["base_url_set"] else "no",
            row["base_url_source"],
            row["api_key_env"],
            "yes" if row["api_key_set"] else "no",
            row["api_key_source"],
            "ready" if row["ready"] else "missing",
        ]
        for row in report["tiers"]
    ]
    lines = [
        "# Idea Workbench Doctor",
        "",
        md_table(["tier", "model", "reasoning", "base_url_env", "base_url", "url_source", "key_env", "key", "key_source", "status"], rows),
        "",
        "## Paper Search MCP",
        "",
        f"- available: {report['paper_search_mcp']['available']}",
        f"- path: {report['paper_search_mcp']['path'] or 'N/A'}",
        "",
        "## Evidence QA",
        "",
        f"- available: {report['evidence_qa']['available']}",
        f"- backend: {report['evidence_qa']['backend']}",
        f"- pqa_path: {report['evidence_qa'].get('pqa_path') or 'N/A'}",
        f"- paperqa_import: {report['evidence_qa'].get('paperqa_import')}",
        f"- reason: {report['evidence_qa']['reason']}",
        "",
        "## Notes",
        "",
    ]
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines).strip() + "\n"


def run_deep(
    project: IdeaProject,
    *,
    dry_run: bool = False,
    allow_fallback: bool = False,
    offline_search: bool = False,
    limit: int | None = None,
    sources: list[str] | None = None,
) -> Path:
    config = load_config(project)
    trace = TraceLogger(project.traces_dir)
    seed_text = read_text(project.seed_path)

    if dry_run:
        return write_dry_run(project, config, seed_text)

    assert_llm_ready(config)

    brief = extract_brief(config, trace, seed_text)
    claims_doc = decompose_claims(config, trace, seed_text, brief)
    queries = plan_queries(config, trace, seed_text, brief, claims_doc)

    write_json(project.state_dir / "brief.json", brief)
    write_json(project.state_dir / "claims.json", claims_doc)
    write_text(project.reports_dir / "research_brief.md", render_brief(brief))

    decomposition = claims_to_decomposition(brief, claims_doc)
    write_json(project.state_dir / "decomposition.json", decomposition)
    write_text(project.reports_dir / "decomposition.md", render_decomposition(decomposition))
    write_text(project.queries_path, render_queries(queries))
    write_json(project.state_dir / "queries.json", queries)

    max_results = limit or int(config.get("max_results_per_query", 5))
    search_sources = sources or parse_sources_from_config(config)
    papers, errors = run_search(queries, sources=search_sources, limit=max_results, offline=offline_search)
    write_json(project.papers_dir / "api_papers.json", papers)
    papers = merge_paper_lists(papers, load_project_papers(project))
    write_json(project.logs_dir / "search_errors.json", errors)
    write_text(project.reports_dir / "search_log.md", render_search_log(queries, papers, errors))

    evidence = run_evidence_qa(project, config, claims_doc, papers)

    matrix = build_llm_matrix(config, trace, brief, claims_doc, papers, evidence)
    write_json(project.state_dir / "novelty_matrix.json", matrix)
    write_text(project.reports_dir / "novelty_matrix.md", render_matrix(matrix))

    review = review_project(config, trace, brief, claims_doc, matrix)
    write_json(project.state_dir / "reviewer_report.json", review)
    write_text(project.reports_dir / "reviewer_report.md", render_review(review))

    ideas = refine_with_llm(config, trace, brief, matrix, review)
    write_json(project.state_dir / "refined_ideas.json", ideas)
    write_text(project.reports_dir / "refined_ideas.md", render_llm_ideas(ideas))

    experiment = plan_experiment_with_llm(config, trace, brief, matrix, review, ideas)
    write_json(project.state_dir / "experiment_plan.json", experiment)
    write_text(project.reports_dir / "experiment_plan.md", render_llm_experiment(experiment))

    final_path = project.reports_dir / "final_report_cn.md"
    write_text(
        final_path,
        render_final_report(
            read_text(project.reports_dir / "decomposition.md"),
            read_text(project.reports_dir / "novelty_matrix.md"),
            read_text(project.reports_dir / "refined_ideas.md"),
            read_text(project.reports_dir / "experiment_plan.md"),
        )
        + "\n---\n\n"
        + read_text(project.reports_dir / "evidence_qa.md")
        + "\n---\n\n"
        + read_text(project.reports_dir / "reviewer_report.md"),
    )
    return final_path


def run_literature(project: IdeaProject, *, offline: bool, limit: int | None, sources: list[str] | None) -> Path:
    config = load_config(project)
    decomposition = read_json(project.state_dir / "decomposition.json", {})
    if not decomposition:
        fallback = decompose_seed(read_text(project.seed_path), config)
        decomposition = fallback
        write_json(project.state_dir / "decomposition.json", decomposition)
    from .heuristics import generate_queries

    queries = read_json(project.state_dir / "queries.json", [])
    if not queries:
        queries = generate_queries(decomposition, config)
    write_text(project.queries_path, render_queries(queries))
    search_sources = sources or parse_sources_from_config(config)
    papers, errors = run_search(
        queries,
        sources=search_sources,
        limit=limit or int(config.get("max_results_per_query", 5)),
        offline=offline,
    )
    write_json(project.papers_dir / "api_papers.json", papers)
    write_json(project.logs_dir / "search_errors.json", errors)
    path = project.reports_dir / "search_log.md"
    write_text(path, render_search_log(queries, papers, errors))
    return path


def run_evidence(project: IdeaProject, *, mock: bool | None = None) -> Path:
    config = load_config(project)
    claims = read_json(project.state_dir / "claims.json", {})
    if not claims:
        decomposition = read_json(project.state_dir / "decomposition.json", {})
        claims = {"claims": decomposition.get("claims", []), "risk_questions": decomposition.get("risk_questions", [])}
    papers = load_project_papers(project)
    run_evidence_qa(project, config, claims, papers, mock=mock)
    return project.reports_dir / "evidence_qa.md"


def load_project_papers(project: IdeaProject) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for path in sorted(project.papers_dir.glob("*.json")):
        data = read_json(path, [])
        if isinstance(data, list):
            papers.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict) and isinstance(data.get("papers"), list):
            papers.extend(item for item in data["papers"] if isinstance(item, dict))
    return merge_paper_lists([], papers)


def merge_paper_lists(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for paper in primary + extra:
        key = paper_key(paper)
        if not key:
            continue
        if key not in by_key:
            item = dict(paper)
            by_key[key] = item
            merged.append(item)
            continue
        existing = by_key[key]
        for field, value in paper.items():
            if value in ("", None, [], {}):
                continue
            if field in {"local_pdf", "pdf_path", "pdf_url"} or not existing.get(field):
                existing[field] = value
    return merged


def paper_key(paper: dict[str, Any]) -> str:
    title = str(paper.get("title") or "").strip().lower()
    if title:
        return "title:" + " ".join(title.split())
    url = str(paper.get("url") or paper.get("pdf_url") or "").strip().lower()
    if url:
        return "url:" + url
    return ""


def run_review(project: IdeaProject, *, dry_run: bool = False) -> Path:
    config = load_config(project)
    trace = TraceLogger(project.traces_dir)
    if dry_run:
        seed_text = read_text(project.seed_path)
        return write_dry_run(project, config, seed_text)
    assert_llm_ready(config, tiers=("frontier",))
    brief = read_json(project.state_dir / "brief.json", {})
    claims = read_json(project.state_dir / "claims.json", {})
    matrix = read_json(project.state_dir / "novelty_matrix.json", {})
    if not brief or not claims or not matrix:
        raise ModelConfigError("review requires brief, claims, and novelty_matrix; run run-deep first")
    review = review_project(config, trace, brief, claims, matrix)
    write_json(project.state_dir / "reviewer_report.json", review)
    path = project.reports_dir / "reviewer_report.md"
    write_text(path, render_review(review))
    return path


def assert_llm_ready(config: dict[str, Any], tiers: tuple[str, ...] = ("cheap", "standard", "strong", "frontier")) -> None:
    missing = []
    for tier_name in tiers:
        tier = get_model_tier(config, tier_name)
        if not tier.ready:
            missing.append(f"{tier.name} ({tier.base_url_env}, {tier.api_key_env})")
    if missing:
        raise ModelConfigError(
            "LLM API is not configured. Set GPT_API_BASE_URL and GPT_API_KEY, or run with --dry-run. Missing: "
            + "; ".join(missing)
        )


def extract_brief(config: dict[str, Any], trace: TraceLogger, seed_text: str) -> dict[str, Any]:
    return call_json(
        config,
        "standard",
        prompt_messages("brief_extractor", {"schema": BRIEF_SCHEMA, "seed": seed_text}),
        trace=trace,
        stage="brief_extractor",
        validator=normalize_brief,
    )


def decompose_claims(config: dict[str, Any], trace: TraceLogger, seed_text: str, brief: dict[str, Any]) -> dict[str, Any]:
    return call_json(
        config,
        "standard",
        prompt_messages("claim_decomposer", {"schema": CLAIMS_SCHEMA, "seed": seed_text, "brief": brief}),
        trace=trace,
        stage="claim_decomposer",
        validator=normalize_claims,
    )


def plan_queries(config: dict[str, Any], trace: TraceLogger, seed_text: str, brief: dict[str, Any], claims: dict[str, Any]) -> list[dict[str, str]]:
    return call_json(
        config,
        "cheap",
        prompt_messages("query_planner", {"schema": QUERY_SCHEMA, "seed": seed_text, "brief": brief, "claims": claims}),
        trace=trace,
        stage="query_planner",
        validator=normalize_queries,
    )


def build_llm_matrix(
    config: dict[str, Any],
    trace: TraceLogger,
    brief: dict[str, Any],
    claims: dict[str, Any],
    papers: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not papers:
        fallback = build_novelty_matrix(claims_to_decomposition(brief, claims), [], config)
        fallback["overall_recommendation"] = "proceed_with_caution"
        return fallback
    return call_json(
        config,
        "strong",
        prompt_messages(
            "novelty_matrix_builder",
            {
                "schema": NOVELTY_SCHEMA,
                "brief": brief,
                "claims": claims,
                "papers": papers[:60],
                "evidence_qa": evidence or {},
            },
        ),
        trace=trace,
        stage="novelty_matrix_builder",
        validator=normalize_matrix,
        temperature=0.1,
    )


def review_project(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], claims: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    return call_json(
        config,
        "frontier",
        prompt_messages("adversarial_reviewer", {"schema": REVIEW_SCHEMA, "brief": brief, "claims": claims, "novelty_matrix": matrix}),
        trace=trace,
        stage="adversarial_reviewer",
        validator=normalize_review,
        temperature=0.1,
    )


def refine_with_llm(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], matrix: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    return call_json(
        config,
        "strong",
        prompt_messages("idea_refiner", {"schema": IDEA_SCHEMA, "brief": brief, "novelty_matrix": matrix, "review": review}),
        trace=trace,
        stage="idea_refiner",
        validator=normalize_ideas,
    )


def plan_experiment_with_llm(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], matrix: dict[str, Any], review: dict[str, Any], ideas: list[dict[str, Any]]) -> dict[str, Any]:
    return call_json(
        config,
        "standard",
        prompt_messages("experiment_planner", {"schema": EXPERIMENT_SCHEMA, "brief": brief, "novelty_matrix": matrix, "review": review, "ideas": ideas}),
        trace=trace,
        stage="experiment_planner",
        validator=normalize_experiment,
    )


def prompt_messages(prompt_name: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    prompt = read_prompt(prompt_name)
    user = (
        f"Prompt id: {prompt_name}\n"
        "Return only valid JSON. Do not wrap in Markdown.\n\n"
        "Input JSON:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ]


def read_prompt(prompt_name: str) -> str:
    path = PROMPT_DIR / f"{prompt_name}.md"
    return path.read_text(encoding="utf-8")


def write_dry_run(project: IdeaProject, config: dict[str, Any], seed_text: str) -> Path:
    trace = TraceLogger(project.traces_dir)
    payloads = {
        "brief_extractor": prompt_messages("brief_extractor", {"schema": BRIEF_SCHEMA, "seed": seed_text}),
        "claim_decomposer": prompt_messages("claim_decomposer", {"schema": CLAIMS_SCHEMA, "seed": seed_text}),
        "query_planner": prompt_messages("query_planner", {"schema": QUERY_SCHEMA, "seed": seed_text}),
        "novelty_matrix_builder": prompt_messages("novelty_matrix_builder", {"schema": NOVELTY_SCHEMA}),
        "adversarial_reviewer": prompt_messages("adversarial_reviewer", {"schema": REVIEW_SCHEMA}),
        "idea_refiner": prompt_messages("idea_refiner", {"schema": IDEA_SCHEMA}),
        "experiment_planner": prompt_messages("experiment_planner", {"schema": EXPERIMENT_SCHEMA}),
    }
    trace.write_artifact("dry_run_prompts", "json", payloads)
    path = project.reports_dir / "run_deep_dry_run.md"
    write_text(
        path,
        "# run-deep dry run\n\n"
        "LLM was not called. Prompt payloads were written to `traces/dry_run_prompts.json`.\n\n"
        "Required environment for live run:\n\n"
        "```bash\n"
        "export GPT_API_BASE_URL=\"https://your-relay.example.com/v1\"\n"
        "export GPT_API_KEY=\"your-relay-key\"\n"
        "```\n",
    )
    return path


def claims_to_decomposition(brief: dict[str, Any], claims_doc: dict[str, Any]) -> dict[str, Any]:
    terms = []
    for key in ("domain", "known_context"):
        terms.extend(str(item) for item in brief.get(key, []) if str(item).strip())
    return {
        "topic": brief.get("topic", "未命名科研想法"),
        "terms": terms,
        "claims": claims_doc.get("claims", []),
        "risk_questions": claims_doc.get("risk_questions", []),
        "suggested_next_step": "阅读 novelty matrix 中高风险 claim 对应论文，再决定是否 pivot。",
    }


def parse_sources_from_config(config: dict[str, Any]) -> list[str]:
    configured = config.get("search_sources", ["arxiv", "openalex", "semantic_scholar"])
    if isinstance(configured, list):
        return [str(item) for item in configured]
    return [item.strip() for item in str(configured).split(",") if item.strip()]


def render_brief(brief: dict[str, Any]) -> str:
    lines = [
        "# Research Brief",
        "",
        f"## Topic\n\n{brief.get('topic', '')}",
        "",
        f"## Problem Statement\n\n{brief.get('problem_statement', '')}",
    ]
    for title, key in [
        ("Domain", "domain"),
        ("Known Context", "known_context"),
        ("Constraints", "constraints"),
        ("Non-goals", "non_goals"),
        ("Success Criteria", "success_criteria"),
        ("Uncertainties", "uncertainties"),
    ]:
        lines.extend(["", f"## {title}", ""])
        for item in brief.get(key, []):
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def render_review(review: dict[str, Any]) -> str:
    lines = [
        "# Adversarial Reviewer Report",
        "",
        f"- Score: {review.get('score', '')}/10",
        f"- Recommendation: {review.get('recommendation', '')}",
        "",
        "## Summary",
        "",
        review.get("summary", ""),
    ]
    for title, key in [
        ("Strongest Objections", "strongest_objections"),
        ("Minimum Fixes", "minimum_fixes"),
        ("Likely Prior Work Attack", "reviewer_likely_prior_work_attack"),
        ("Experiment Concerns", "experiment_concerns"),
    ]:
        lines.extend(["", f"## {title}", ""])
        for item in review.get(key, []):
            lines.append(f"- {item}")
    lines.extend(["", "## Positioning Advice", "", review.get("positioning_advice", "")])
    return "\n".join(lines).strip() + "\n"


def render_llm_ideas(ideas: list[dict[str, Any]]) -> str:
    lines = ["# 打磨后的候选 Idea", ""]
    for idea in sorted(ideas, key=lambda item: item.get("rank", 999)):
        lines.extend(
            [
                f"## {idea.get('rank', '')}. {idea.get('name', '')}",
                "",
                f"- 研究问题：{idea.get('research_question', '')}",
                f"- 新颖性杠杆：{idea.get('novelty_lever', '')}",
                f"- 最小实验：{idea.get('minimum_experiment', '')}",
                f"- 主要风险：{idea.get('main_risk', '')}",
                f"- 贡献类型：{idea.get('expected_contribution', '')}",
                "",
                "方法步骤：",
            ]
        )
        for step in idea.get("method", []):
            lines.append(f"- {step}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_llm_experiment(plan: dict[str, Any]) -> str:
    base = render_experiment_plan(plan)
    lines = [base.strip(), "", "## Ablations", ""]
    for item in plan.get("ablations", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Results to Claims", ""])
    rows = [
        [item.get("possible_result", ""), item.get("allowed_claim", ""), item.get("forbidden_claim", "")]
        for item in plan.get("results_to_claims", [])
    ]
    if rows:
        lines.append(md_table(["possible result", "allowed claim", "forbidden claim"], rows))
    else:
        lines.append("暂无。")
    return "\n".join(lines).strip() + "\n"

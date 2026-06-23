from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import detect_evidence_backend, run_evidence_qa
from .heuristics import build_experiment_plan, build_novelty_matrix, decompose_seed, refine_ideas
from .literature_store import build_or_load_literature_store, retrieve_novelty_claim_context, retrieve_stage_context
from .models import ModelConfigError, call_json, doctor as model_doctor, get_model_tier
from .project import IdeaProject, detail_report_path, load_config, read_detail_report, read_json, read_text, write_json, write_text
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
    BOTTLENECK_SCHEMA,
    BRANCH_SCREEN_SCHEMA,
    BRIEF_SCHEMA,
    CLAIMS_SCHEMA,
    EXPERIMENT_SCHEMA,
    IDEA_SCHEMA,
    IDEA_BRANCH_SCHEMA,
    IDEA_SEARCH_RESULT_SCHEMA,
    MECHANISM_TRANSFER_SCHEMA,
    NOVELTY_SCHEMA,
    QUERY_SCHEMA,
    RESEARCH_CRITIC_SCHEMA,
    RESEARCH_DECISION_SCHEMA,
    RESEARCH_IDEA_SCHEMA,
    RESEARCH_OPPORTUNITY_SCHEMA,
    RESEARCH_REVISION_SCHEMA,
    REVIEW_SCHEMA,
    STRENGTHENED_IDEAS_SCHEMA,
    normalize_bottlenecks,
    normalize_branch_screen,
    normalize_brief,
    normalize_claims,
    normalize_experiment,
    normalize_idea_branches,
    normalize_idea_search_result,
    normalize_ideas,
    normalize_matrix,
    normalize_mechanism_transfers,
    normalize_queries,
    normalize_research_critic,
    normalize_research_decision,
    normalize_research_ideas,
    normalize_research_opportunities,
    normalize_research_revision,
    normalize_review,
    normalize_strengthened_ideas,
)
from .search import run_search
from .tracing import TraceLogger, text_hash


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def progress(message: str) -> None:
    print(f"[idea-workbench] {message}", flush=True)


def log_progress(progress_fn, message: str) -> None:
    if progress_fn:
        progress_fn(message)


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
    progress("run-deep: start")

    stage_dir = project.state_dir / "run_deep_stages"
    stage_dir.mkdir(parents=True, exist_ok=True)

    brief = cached_json_stage(
        stage_dir,
        "brief",
        project.state_dir / "brief.json",
        {"stage_version": 1, "seed": seed_text},
        lambda: extract_brief(config, trace, seed_text),
        accept=lambda value: isinstance(value, dict) and bool(value.get("topic") or value.get("problem_statement")),
        progress=progress,
        label="brief [standard]",
    )
    claims_doc = cached_json_stage(
        stage_dir,
        "claims",
        project.state_dir / "claims.json",
        {"stage_version": 1, "seed": seed_text, "brief": brief},
        lambda: decompose_claims(config, trace, seed_text, brief),
        accept=lambda value: isinstance(value, dict) and bool(value.get("claims")),
        progress=progress,
        label="claims [standard]",
    )
    queries = cached_json_stage(
        stage_dir,
        "queries",
        project.state_dir / "queries.json",
        {"stage_version": 1, "seed": seed_text, "brief": brief, "claims": claims_doc},
        lambda: plan_queries(config, trace, seed_text, brief, claims_doc),
        accept=lambda value: isinstance(value, list) and bool(value),
        progress=progress,
        label="query planning [cheap]",
    )

    write_json(project.state_dir / "brief.json", brief)
    write_json(project.state_dir / "claims.json", claims_doc)
    write_text(detail_report_path(project, "research_brief.md"), render_brief(brief))

    decomposition = claims_to_decomposition(brief, claims_doc)
    write_json(project.state_dir / "decomposition.json", decomposition)
    write_text(detail_report_path(project, "decomposition.md"), render_decomposition(decomposition))
    write_text(project.queries_path, render_queries(queries))
    write_json(project.state_dir / "queries.json", queries)

    max_results = limit or int(config.get("max_results_per_query", 5))
    search_sources = sources or parse_sources_from_config(config)
    search_result = cached_json_stage(
        stage_dir,
        "paper_search",
        stage_dir / "paper_search.json",
        {
            "stage_version": 1,
            "queries": queries,
            "sources": search_sources,
            "limit": max_results,
            "offline": offline_search,
        },
        lambda: dict_from_search_result(run_search(queries, sources=search_sources, limit=max_results, offline=offline_search)),
        accept=lambda value: isinstance(value, dict) and isinstance(value.get("papers"), list),
        progress=progress,
        label="paper search",
    )
    papers = search_result.get("papers", [])
    errors = search_result.get("errors", [])
    write_json(project.papers_dir / "api_papers.json", papers)
    papers = merge_paper_lists(papers, load_project_papers(project))
    write_json(project.logs_dir / "search_errors.json", errors)
    write_text(detail_report_path(project, "search_log.md"), render_search_log(queries, papers, errors))
    progress(f"paper search: {len(papers)} papers, {len(errors)} notes/errors")

    evidence = run_evidence_qa(project, config, claims_doc, papers)
    progress(f"evidence QA: {evidence.get('status', '')}")

    matrix = cached_json_stage(
        stage_dir,
        "novelty_matrix_v2",
        stage_dir / "novelty_matrix_v2_merged.json",
        {
            "stage_version": 2,
            "brief": brief,
            "claims": claims_doc,
            "papers": papers_for_cache(papers),
            "evidence": evidence_for_cache(evidence),
            "batch_size": max(1, int(config.get("novelty_matrix_claim_batch_size", 1))),
        },
        lambda: build_llm_matrix(config, trace, project, brief, claims_doc, papers, evidence, progress=progress),
        accept=lambda value: isinstance(value, dict) and bool(value.get("rows")),
        progress=progress,
        label="novelty matrix [strong]",
    )
    write_json(project.state_dir / "novelty_matrix.json", matrix)
    write_text(detail_report_path(project, "novelty_matrix.md"), render_matrix(matrix))

    review = cached_json_stage(
        stage_dir,
        "reviewer_report",
        project.state_dir / "reviewer_report.json",
        {"stage_version": 2, "brief": brief, "claims": claims_doc, "novelty_matrix": matrix},
        lambda: review_project(config, trace, brief, claims_doc, matrix),
        accept=lambda value: isinstance(value, dict) and bool(value.get("summary")),
        progress=progress,
        label="adversarial review [frontier]",
    )
    write_json(project.state_dir / "reviewer_report.json", review)
    write_text(detail_report_path(project, "reviewer_report.md"), render_review(review))

    ideas = cached_json_stage(
        stage_dir,
        "refined_ideas",
        project.state_dir / "refined_ideas.json",
        {"stage_version": 2, "brief": brief, "novelty_matrix": matrix, "review": review},
        lambda: refine_with_llm(config, trace, brief, matrix, review),
        accept=lambda value: isinstance(value, list) and bool(value),
        progress=progress,
        label="idea refinement [strong]",
    )
    write_json(project.state_dir / "refined_ideas.json", ideas)
    write_text(detail_report_path(project, "refined_ideas.md"), render_llm_ideas(ideas))

    experiment = cached_json_stage(
        stage_dir,
        "experiment_plan",
        project.state_dir / "experiment_plan.json",
        {"stage_version": 2, "brief": brief, "novelty_matrix": matrix, "review": review, "ideas": ideas},
        lambda: plan_experiment_with_llm(config, trace, brief, matrix, review, ideas),
        accept=lambda value: isinstance(value, dict) and bool(value.get("objective")),
        progress=progress,
        label="experiment plan [standard]",
    )
    write_json(project.state_dir / "experiment_plan.json", experiment)
    write_text(detail_report_path(project, "experiment_plan.md"), render_llm_experiment(experiment))

    evidence_pack = (
        "# run-deep Evidence Pack\n\n"
        "这个文件是 research 阶段的证据输入包，不是最终研究方案报告。最终 proposal 请看 `reports/research.md`。\n\n"
        + render_final_report(
            read_detail_report(project, "decomposition.md"),
            read_detail_report(project, "novelty_matrix.md"),
            read_detail_report(project, "refined_ideas.md"),
            read_detail_report(project, "experiment_plan.md"),
        )
        + "\n---\n\n"
        + read_detail_report(project, "evidence_qa.md")
        + "\n---\n\n"
        + read_detail_report(project, "reviewer_report.md")
    )
    evidence_pack_path = project.reports_dir / "evidence_pack_cn.md"
    write_text(evidence_pack_path, evidence_pack)
    # Compatibility for existing scripts/tests that still expect this path.
    write_text(project.reports_dir / "final_report_cn.md", evidence_pack)
    progress(f"run-deep: wrote evidence pack {evidence_pack_path}")
    return evidence_pack_path


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
    path = detail_report_path(project, "search_log.md")
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
    return detail_report_path(project, "evidence_qa.md")


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
    path = detail_report_path(project, "reviewer_report.md")
    write_text(path, render_review(review))
    return path


def run_idea_search(
    project: IdeaProject,
    *,
    branches: int = 20,
    shortlist: int = 5,
    final: int = 3,
    dry_run: bool = False,
    refresh_evidence_store: bool = False,
) -> Path:
    config = load_config(project)
    trace = TraceLogger(project.traces_dir)
    base_context = load_idea_search_base_context(project, command_name="idea-search")
    params = {
        "branches": max(1, branches),
        "shortlist": max(1, shortlist),
        "final": max(1, final),
    }
    if not dry_run:
        assert_llm_ready(config, tiers=("strong", "frontier"))
        progress("idea-search: start")

    papers = load_project_papers(project)
    literature_store = build_or_load_literature_store(
        project,
        papers=papers,
        brief=base_context["brief"],
        claims=base_context["claims"],
        novelty_matrix=base_context["novelty_matrix"],
        reviewer_report=base_context["reviewer_report"],
        evidence_qa=base_context["evidence_qa"],
        refresh=refresh_evidence_store,
        progress=progress if not dry_run else None,
    )

    if dry_run:
        return write_idea_search_dry_run(project, base_context, literature_store, params)

    stage_dir = project.state_dir / "idea_search_stages"
    stage_dir.mkdir(parents=True, exist_ok=True)
    if refresh_evidence_store:
        clear_stage_cache(stage_dir)

    bottleneck_context = retrieve_stage_context(store=literature_store, stage="bottleneck_extractor", base_context=base_context)
    bottlenecks = cached_stage(
        stage_dir / "bottlenecks.json",
        {"stage_version": 2, "context": bottleneck_context},
        lambda: extract_bottlenecks(config, trace, bottleneck_context),
        progress=progress,
        label="bottlenecks [strong]",
    )

    transfer_context = retrieve_stage_context(
        store=literature_store,
        stage="mechanism_transfer_mapper",
        base_context=base_context,
        extra_context={"bottleneck_summary": summarize_bottlenecks(bottlenecks)},
    )
    transfers = cached_stage(
        stage_dir / "mechanism_transfers.json",
        {"stage_version": 2, "context": transfer_context, "bottlenecks": summarize_bottlenecks(bottlenecks)},
        lambda: map_mechanism_transfers(config, trace, transfer_context, bottlenecks),
        progress=progress,
        label="mechanism transfer [strong]",
    )

    branches_doc = generate_idea_branches_batched(
        config,
        trace,
        stage_dir,
        literature_store,
        base_context,
        bottlenecks,
        transfers,
        params["branches"],
        progress=progress,
    )

    screen_context = retrieve_stage_context(
        store=literature_store,
        stage="branch_screener",
        base_context=base_context,
        extra_context={"branches": summarize_branches(branches_doc)},
        include_extra_context=False,
    )
    screen = cached_stage(
        stage_dir / f"screen_{params['shortlist']}.json",
        {
            "stage_version": 2,
            "context": screen_context,
            "branches": summarize_branches(branches_doc),
            "shortlist": params["shortlist"],
        },
        lambda: screen_idea_branches(config, trace, screen_context, branches_doc, params["shortlist"]),
        progress=progress,
        label=f"branch screen [strong, top {params['shortlist']}]",
    )

    strengthener_context = retrieve_stage_context(
        store=literature_store,
        stage="idea_strengthener",
        base_context=base_context,
        extra_context={"branches": summarize_branches(branches_doc), "screen_summary": summarize_screen(screen)},
        include_extra_context=False,
    )
    strengthened = cached_stage(
        stage_dir / "strengthened_ideas.json",
        {
            "stage_version": 2,
            "context": strengthener_context,
            "branches": summarize_branches(branches_doc),
            "screen": summarize_screen(screen),
        },
        lambda: strengthen_ideas(config, trace, strengthener_context, branches_doc, screen),
        progress=progress,
        label="idea strengthening [strong]",
    )

    decision_context = retrieve_stage_context(
        store=literature_store,
        stage="decision_chair",
        base_context=base_context,
        extra_context={
            "bottleneck_summary": summarize_bottlenecks(bottlenecks),
            "transfer_summary": summarize_transfers(transfers),
            "screen_summary": summarize_screen(screen),
            "strengthened_summary": summarize_strengthened(strengthened),
        },
        include_extra_context=False,
    )
    final_result = cached_stage(
        stage_dir / f"decision_{params['final']}.json",
        {
            "stage_version": 2,
            "context": decision_context,
            "bottlenecks": summarize_bottlenecks(bottlenecks),
            "transfers": summarize_transfers(transfers),
            "screen": summarize_screen(screen),
            "strengthened": summarize_strengthened(strengthened),
            "final": params["final"],
        },
        lambda: decide_ideas(config, trace, decision_context, bottlenecks, transfers, branches_doc, screen, strengthened, params["final"]),
        progress=progress,
        label=f"final decision [frontier, top {params['final']}]",
    )

    result = {
        "parameters": params,
        "literature_store": {
            "papers": literature_store.get("paper_count", 0),
            "passages": literature_store.get("passage_count", 0),
            "evidence_items": literature_store.get("evidence_count", 0),
        },
        "bottlenecks": bottlenecks,
        "mechanism_transfers": transfers,
        "branches": branches_doc,
        "screen": screen,
        "strengthened_ideas": strengthened,
        "final": final_result,
    }
    write_json(project.state_dir / "idea_search.json", result)
    path = project.reports_dir / "idea_search.md"
    write_text(path, render_idea_search(result))
    progress(f"idea-search: wrote {path}")
    return path


def run_research(
    project: IdeaProject,
    *,
    ideas: int = 5,
    final: int = 3,
    refresh_evidence_store: bool = False,
) -> Path:
    config = load_config(project)
    trace = TraceLogger(project.traces_dir)
    base_context = load_idea_search_base_context(project, command_name="research")
    params = {"ideas": max(1, ideas), "final": max(1, final)}

    assert_llm_ready(config, tiers=("strong", "frontier"))
    progress("research: start")

    papers = load_project_papers(project)
    literature_store = build_or_load_literature_store(
        project,
        papers=papers,
        brief=base_context["brief"],
        claims=base_context["claims"],
        novelty_matrix=base_context["novelty_matrix"],
        reviewer_report=base_context["reviewer_report"],
        evidence_qa=base_context["evidence_qa"],
        refresh=refresh_evidence_store,
        progress=progress,
    )

    stage_dir = project.state_dir / "research_stages"
    stage_dir.mkdir(parents=True, exist_ok=True)
    if refresh_evidence_store:
        clear_stage_cache(stage_dir)

    shared = {
        "quality_bar": read_prompt("research_quality_bar"),
        "language_instruction": language_instruction(config),
    }
    opportunity_context = retrieve_stage_context(
        store=literature_store,
        stage="research_opportunity_miner",
        base_context=base_context,
    )
    opportunities = cached_stage(
        stage_dir / "opportunities.json",
        {"stage_version": 1, "context": opportunity_context, "shared": shared},
        lambda: mine_research_opportunities(config, trace, opportunity_context, shared),
        progress=progress,
        label="opportunity mining [strong]",
    )

    builder_context = retrieve_stage_context(
        store=literature_store,
        stage="research_builder",
        base_context=base_context,
        extra_context={"opportunities": summarize_research_opportunities(opportunities)},
    )
    initial_ideas = cached_stage(
        stage_dir / f"initial_ideas_{params['ideas']}.json",
        {
            "stage_version": 1,
            "context": builder_context,
            "opportunities": summarize_research_opportunities(opportunities),
            "ideas": params["ideas"],
            "shared": shared,
        },
        lambda: build_research_ideas(config, trace, builder_context, opportunities, params["ideas"], shared),
        progress=progress,
        label=f"builder round [strong, {params['ideas']}]",
    )

    critic_context = retrieve_stage_context(
        store=literature_store,
        stage="research_critic_panel",
        base_context=base_context,
        extra_context={"ideas": summarize_research_ideas(initial_ideas)},
    )
    critic = cached_stage(
        stage_dir / "critic_panel.json",
        {
            "stage_version": 1,
            "context": critic_context,
            "ideas": summarize_research_ideas(initial_ideas),
            "shared": shared,
        },
        lambda: critique_research_ideas(config, trace, critic_context, initial_ideas, shared),
        progress=progress,
        label="comprehensive critic [frontier]",
    )

    reviser_context = retrieve_stage_context(
        store=literature_store,
        stage="research_reviser",
        base_context=base_context,
        extra_context={"ideas": summarize_research_ideas(initial_ideas), "critic": summarize_research_critic(critic)},
    )
    revised = cached_stage(
        stage_dir / "revised_ideas.json",
        {
            "stage_version": 2,
            "context": reviser_context,
            "ideas": summarize_research_ideas(initial_ideas),
            "critic": summarize_research_critic(critic),
            "shared": shared,
        },
        lambda: revise_research_ideas(config, trace, reviser_context, initial_ideas, critic, shared),
        progress=progress,
        label="builder revision [strong]",
    )

    chair_context = retrieve_stage_context(
        store=literature_store,
        stage="research_chair",
        base_context=base_context,
        extra_context={
            "opportunities": summarize_research_opportunities(opportunities),
            "critic": summarize_research_critic(critic),
            "revised_ideas": summarize_revised_research_ideas(revised),
        },
    )
    decision = cached_stage(
        stage_dir / f"chair_decision_{params['final']}.json",
        {
            "stage_version": 2,
            "context": chair_context,
            "opportunities": summarize_research_opportunities(opportunities),
            "critic": summarize_research_critic(critic),
            "revised": summarize_revised_research_ideas(revised),
            "final": params["final"],
            "shared": shared,
        },
        lambda: decide_research(config, trace, chair_context, opportunities, critic, revised, params["final"], shared),
        progress=progress,
        label=f"research chair [frontier, top {params['final']}]",
    )

    result = {
        "parameters": params,
        "literature_store": {
            "papers": literature_store.get("paper_count", 0),
            "passages": literature_store.get("passage_count", 0),
            "evidence_items": literature_store.get("evidence_count", 0),
        },
        "opportunities": opportunities,
        "initial_ideas": initial_ideas,
        "critic_panel": critic,
        "revised_ideas": revised,
        "final": decision,
    }
    write_json(project.state_dir / "research_workflow.json", result)
    write_text(detail_report_path(project, "research_rounds.md"), render_research_rounds(result))
    path = project.reports_dir / "research.md"
    write_text(path, render_research(result))
    progress(f"research: wrote {path}")
    return path


def load_idea_search_base_context(project: IdeaProject, *, command_name: str = "idea-search") -> dict[str, Any]:
    required = {
        "brief": project.state_dir / "brief.json",
        "claims": project.state_dir / "claims.json",
        "novelty_matrix": project.state_dir / "novelty_matrix.json",
        "reviewer_report": project.state_dir / "reviewer_report.json",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise ModelConfigError(
            f"{command_name} requires run-deep artifacts first. Missing: "
            + ", ".join(missing)
            + ". Run `python3 -m idea_workbench run-deep <project>` first."
        )
    return {
        "brief": read_json(required["brief"], {}),
        "claims": read_json(required["claims"], {}),
        "novelty_matrix": read_json(required["novelty_matrix"], {}),
        "reviewer_report": read_json(required["reviewer_report"], {}),
        "evidence_qa": read_json(project.evidence_dir / "evidence_status.json", {}),
    }


def cached_json_stage(
    stage_dir: Path,
    name: str,
    output_path: Path,
    input_data: Any,
    producer,
    *,
    accept=None,
    progress=None,
    label: str | None = None,
) -> Any:
    input_hash = json_input_hash(input_data)
    meta_path = stage_dir / f"{name}.meta.json"
    display = label or name
    if output_path.exists() and meta_path.exists():
        cached = read_json(output_path, None)
        meta = read_json(meta_path, {})
        if meta.get("input_hash") == input_hash and (accept(cached) if accept else cached not in (None, {}, [])):
            log_progress(progress, f"{display}: cache hit")
            return cached

    log_progress(progress, f"{display}: running")
    result = producer()
    write_json(output_path, result)
    write_json(meta_path, {"input_hash": input_hash})
    return result


def json_input_hash(data: Any) -> str:
    return text_hash(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str))


def dict_from_search_result(result: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> dict[str, Any]:
    papers, errors = result
    return {"papers": papers, "errors": errors}


def papers_for_cache(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        compact.append(
            {
                "title": paper.get("title", ""),
                "year": paper.get("year", "") or paper.get("published_date", ""),
                "url": paper.get("url", ""),
                "doi": paper.get("doi", ""),
                "arxiv_id": paper.get("arxiv_id", ""),
                "pdf_url": paper.get("pdf_url", ""),
                "local_pdf": paper.get("local_pdf", "") or paper.get("pdf_path", ""),
                "abstract": truncate_text(paper.get("abstract", ""), 400),
            }
        )
    return compact


def evidence_for_cache(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": evidence.get("status", ""),
        "backend": evidence.get("backend", ""),
        "reason": evidence.get("reason", ""),
        "item_count": len(evidence.get("items", [])) if isinstance(evidence.get("items", []), list) else 0,
        "selected_count": len(evidence.get("selected_papers", [])) if isinstance(evidence.get("selected_papers", []), list) else 0,
    }


def cached_stage(
    path: Path,
    input_data: Any,
    producer,
    *,
    accept=None,
    progress=None,
    label: str | None = None,
) -> dict[str, Any]:
    input_hash = json_input_hash(input_data)
    meta_path = cache_meta_path(path)
    display = label or path.stem
    if path.exists() and meta_path.exists():
        cached = read_json(path, {})
        meta = read_json(meta_path, {})
        accepted = accept(cached) if accept else isinstance(cached, dict) and bool(cached)
        if meta.get("input_hash") == input_hash and accepted:
            log_progress(progress, f"{display}: cache hit")
            return cached
    log_progress(progress, f"{display}: running")
    result = producer()
    write_json(path, result)
    write_json(meta_path, {"input_hash": input_hash})
    return result


def cache_meta_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.meta.json")


def clear_stage_cache(stage_dir: Path) -> None:
    for path in stage_dir.glob("*.json"):
        if path.is_file():
            path.unlink()
    for path in stage_dir.glob("*.meta.json"):
        if path.is_file():
            path.unlink()


def generate_idea_branches_batched(
    config: dict[str, Any],
    trace: TraceLogger,
    stage_dir: Path,
    literature_store: dict[str, Any],
    base_context: dict[str, Any],
    bottlenecks: dict[str, Any],
    transfers: dict[str, Any],
    branch_count: int,
    progress=None,
) -> dict[str, Any]:
    merged_path = stage_dir / f"branches_{branch_count}.json"
    merged_input = {
        "stage_version": 2,
        "branch_count": branch_count,
        "literature_store_signature": literature_store.get("input_signature", {}),
        "base_context": {
            "brief": base_context.get("brief", {}),
            "claims": base_context.get("claims", {}),
            "novelty_matrix": base_context.get("novelty_matrix", {}),
            "reviewer_report": base_context.get("reviewer_report", {}),
        },
        "bottlenecks": summarize_bottlenecks(bottlenecks),
        "transfers": summarize_transfers(transfers),
    }
    merged_hash = json_input_hash(merged_input)
    merged_meta = cache_meta_path(merged_path)
    if merged_path.exists() and merged_meta.exists():
        cached = read_json(merged_path, {})
        meta = read_json(merged_meta, {})
        if meta.get("input_hash") == merged_hash and isinstance(cached, dict) and cached.get("branches"):
            log_progress(progress, f"idea branches [strong, {branch_count}]: cache hit")
            return cached

    log_progress(progress, f"idea branches [strong, {branch_count}]: running")
    batch_size = 5
    batches: list[dict[str, Any]] = []
    generated_so_far: list[dict[str, Any]] = []
    track_focuses = [
        "conservative and diagnostic branches",
        "method and mechanism-transfer branches",
        "failure-analysis and benchmark branches",
        "high-risk and reframing branches",
    ]

    batch_index = 1
    while len(generated_so_far) < branch_count:
        previous_count = len(generated_so_far)
        target = min(batch_size, branch_count - len(generated_so_far))
        batch_path = stage_dir / f"branches_{branch_count}_batch_{batch_index}.json"
        focus = track_focuses[(batch_index - 1) % len(track_focuses)]
        extra_context = {
            "bottleneck_summary": summarize_bottlenecks(bottlenecks),
            "transfer_summary": summarize_transfers(transfers),
            "branch_batch": {
                "batch_index": batch_index,
                "batch_size": target,
                "target_total_branches": branch_count,
                "track_focus": focus,
                "existing_branches": summarize_branch_list(generated_so_far),
            },
        }
        branch_context = retrieve_stage_context(
            store=literature_store,
            stage="idea_branch_generator",
            base_context=base_context,
            extra_context=extra_context,
        )
        batch_doc = cached_stage(
            batch_path,
            {
                "stage_version": 2,
                "target": target,
                "branch_count": branch_count,
                "context": branch_context,
                "bottlenecks": summarize_bottlenecks(bottlenecks),
                "transfers": summarize_transfers(transfers),
            },
            lambda target=target, branch_context=branch_context: generate_idea_branches(
                config,
                trace,
                branch_context,
                bottlenecks,
                transfers,
                target,
            ),
            progress=progress,
            label=f"branch batch {batch_index} [strong, {target}]",
        )
        batches.append(batch_doc)
        generated_so_far = merge_branch_batches(batches, branch_count)["branches"]
        if len(generated_so_far) <= previous_count:
            break
        if target <= 0:
            break
        batch_index += 1
        if batch_index > 20:
            break

    merged = merge_branch_batches(batches, branch_count)
    write_json(merged_path, merged)
    write_json(merged_meta, {"input_hash": merged_hash})
    return merged


def merge_branch_batches(batches: list[dict[str, Any]], branch_count: int) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch_index, batch in enumerate(batches, start=1):
        for branch in batch.get("branches", []):
            if not isinstance(branch, dict):
                continue
            key = branch_key(branch)
            if key in seen:
                continue
            seen.add(key)
            item = dict(branch)
            item["source_batch"] = batch_index
            branches.append(item)
            if len(branches) >= branch_count:
                break
        if len(branches) >= branch_count:
            break
    for index, branch in enumerate(branches, start=1):
        branch["id"] = f"I{index}"
    return {"branches": branches}


def branch_key(branch: dict[str, Any]) -> str:
    name = " ".join(str(branch.get("name") or "").lower().split())
    core = " ".join(str(branch.get("core_idea") or "").lower().split())
    return name or core[:120] or str(branch.get("id") or "")


def summarize_branches(branches_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return summarize_branch_list(branches_doc.get("branches", []))


def summarize_branch_list(branches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for branch in branches[:30]:
        if not isinstance(branch, dict):
            continue
        summaries.append(
            {
                "id": branch.get("id", ""),
                "track": branch.get("track", ""),
                "name": branch.get("name", ""),
                "core_idea": truncate_text(branch.get("core_idea", ""), 350),
                "closest_prior_work_risk": truncate_text(branch.get("closest_prior_work_risk", ""), 300),
            }
        )
    return summaries


def compact_branches_for_prompt(branches_doc: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    for branch in branches_doc.get("branches", [])[:limit]:
        if not isinstance(branch, dict):
            continue
        branches.append(
            {
                "id": branch.get("id", ""),
                "source_batch": branch.get("source_batch", ""),
                "track": branch.get("track", ""),
                "name": branch.get("name", ""),
                "core_idea": truncate_text(branch.get("core_idea", ""), 260),
                "mechanism": truncate_text(branch.get("mechanism", ""), 240),
                "novelty_hypothesis": truncate_text(branch.get("novelty_hypothesis", ""), 220),
                "minimum_experiment": truncate_text(branch.get("minimum_experiment", ""), 240),
                "falsifiable_prediction": truncate_text(branch.get("falsifiable_prediction", ""), 180),
                "closest_prior_work_risk": truncate_text(branch.get("closest_prior_work_risk", ""), 220),
                "feasibility_risk": truncate_text(branch.get("feasibility_risk", ""), 160),
                "evidence_needed": [truncate_text(item, 120) for item in branch.get("evidence_needed", [])[:3]],
            }
        )
    return {"branches": branches}


def compact_screen_for_prompt(screen: dict[str, Any]) -> dict[str, Any]:
    return {
        "shortlist": [
            {
                "branch_id": item.get("branch_id", ""),
                "decision": item.get("decision", ""),
                "score": item.get("score", ""),
                "rationale": truncate_text(item.get("rationale", ""), 360),
                "fatal_objections": [truncate_text(objection, 220) for objection in item.get("fatal_objections", [])[:4]],
                "salvage_path": truncate_text(item.get("salvage_path", ""), 320),
            }
            for item in screen.get("shortlist", [])[:20]
            if isinstance(item, dict)
        ],
        "discarded": [truncate_text(item, 260) for item in screen.get("discarded", [])[:12]],
    }


def compact_strengthened_for_prompt(strengthened: dict[str, Any]) -> dict[str, Any]:
    return {
        "ideas": [
            {
                "branch_id": item.get("branch_id", ""),
                "name": item.get("name", ""),
                "technical_move": truncate_text(item.get("technical_move", ""), 420),
                "novelty_boundary": truncate_text(item.get("novelty_boundary", ""), 380),
                "minimum_experiment": truncate_text(item.get("minimum_experiment", ""), 420),
                "main_risk": truncate_text(item.get("main_risk", ""), 320),
                "fixes": [truncate_text(fix, 240) for fix in item.get("fixes", [])[:4]],
            }
            for item in strengthened.get("ideas", [])[:20]
            if isinstance(item, dict)
        ]
    }


def summarize_bottlenecks(bottlenecks: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id", ""),
            "description": truncate_text(item.get("description", ""), 300),
            "failure_mode": truncate_text(item.get("failure_mode", ""), 260),
            "evidence_signal": truncate_text(item.get("evidence_signal", ""), 220),
        }
        for item in bottlenecks.get("bottlenecks", [])[:10]
        if isinstance(item, dict)
    ]


def summarize_transfers(transfers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id", ""),
            "source_field": item.get("source_field", ""),
            "source_mechanism": truncate_text(item.get("source_mechanism", ""), 260),
            "target_bottleneck": truncate_text(item.get("target_bottleneck", ""), 180),
            "main_risk": truncate_text(item.get("main_risk", ""), 220),
        }
        for item in transfers.get("transfers", [])[:10]
        if isinstance(item, dict)
    ]


def summarize_screen(screen: dict[str, Any]) -> dict[str, Any]:
    return {
        "shortlist": [
            {
                "branch_id": item.get("branch_id", ""),
                "decision": item.get("decision", ""),
                "score": item.get("score", ""),
                "rationale": truncate_text(item.get("rationale", ""), 260),
                "fatal_objections": item.get("fatal_objections", [])[:3],
            }
            for item in screen.get("shortlist", [])[:12]
            if isinstance(item, dict)
        ],
        "discarded": screen.get("discarded", [])[:8],
    }


def summarize_strengthened(strengthened: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "branch_id": item.get("branch_id", ""),
            "name": item.get("name", ""),
            "technical_move": truncate_text(item.get("technical_move", ""), 280),
            "main_risk": truncate_text(item.get("main_risk", ""), 220),
        }
        for item in strengthened.get("ideas", [])[:12]
        if isinstance(item, dict)
    ]


def language_instruction(config: dict[str, Any]) -> str:
    language = str(config.get("language", "zh") or "zh").lower()
    if language in {"en", "english"}:
        return "Use English for human-facing strings."
    if language in {"bilingual", "zh-en", "zh_en"}:
        return (
            "Use Chinese as the primary language and add concise English equivalents only where useful. "
            "Keep established technical terms in English."
        )
    return (
        "Use Chinese for human-facing reasoning and report text. Keep established technical terms in English, "
        "including Transformer, RL, WAM, diffusion model, world model, benchmark, baseline, representation learning, "
        "and model-based control."
    )


def summarize_research_opportunities(opportunities: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id", ""),
            "bottleneck": truncate_text(item.get("bottleneck", ""), 280),
            "why_important": truncate_text(item.get("why_important", ""), 220),
            "novelty_path": item.get("novelty_path", ""),
            "risk": truncate_text(item.get("risk", ""), 180),
        }
        for item in opportunities.get("bottleneck_opportunities", [])[:10]
        if isinstance(item, dict)
    ]


def compact_research_opportunities(opportunities: dict[str, Any]) -> dict[str, Any]:
    return {
        "bottleneck_opportunities": [
            {
                "id": item.get("id", ""),
                "bottleneck": truncate_text(item.get("bottleneck", ""), 360),
                "why_important": truncate_text(item.get("why_important", ""), 280),
                "evidence_signal": truncate_text(item.get("evidence_signal", ""), 260),
                "mechanism_transfer_candidates": [
                    truncate_text(candidate, 160) for candidate in item.get("mechanism_transfer_candidates", [])[:5]
                ],
                "novelty_path": item.get("novelty_path", ""),
                "risk": truncate_text(item.get("risk", ""), 220),
                "evidence_needed": [truncate_text(evidence, 140) for evidence in item.get("evidence_needed", [])[:4]],
            }
            for item in opportunities.get("bottleneck_opportunities", [])[:10]
            if isinstance(item, dict)
        ],
        "quality_bar_notes": [truncate_text(note, 180) for note in opportunities.get("quality_bar_notes", [])[:6]],
    }


def summarize_research_ideas(ideas: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "central_insight": truncate_text(item.get("central_insight", ""), 260),
            "technical_move": truncate_text(item.get("technical_move", ""), 220),
            "minimum_discriminating_experiment": truncate_text(item.get("minimum_discriminating_experiment", ""), 220),
            "maturity": item.get("maturity", ""),
        }
        for item in ideas.get("ideas", [])[:12]
        if isinstance(item, dict)
    ]


def compact_research_ideas(ideas: dict[str, Any]) -> dict[str, Any]:
    return {
        "ideas": [
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "seed_source": item.get("seed_source", ""),
                "central_insight": truncate_text(item.get("central_insight", ""), 360),
                "problem_framing": truncate_text(item.get("problem_framing", ""), 300),
                "nontrivial_mechanism_match": truncate_text(item.get("nontrivial_mechanism_match", ""), 320),
                "technical_move": truncate_text(item.get("technical_move", ""), 320),
                "novelty_boundary": truncate_text(item.get("novelty_boundary", ""), 300),
                "stronger_baseline_to_beat": truncate_text(item.get("stronger_baseline_to_beat", ""), 240),
                "minimum_discriminating_experiment": truncate_text(item.get("minimum_discriminating_experiment", ""), 300),
                "falsifiable_prediction": truncate_text(item.get("falsifiable_prediction", ""), 240),
                "failure_value": truncate_text(item.get("failure_value", ""), 240),
                "main_risks": [truncate_text(risk, 160) for risk in item.get("main_risks", [])[:4]],
                "evidence_needed": [truncate_text(evidence, 140) for evidence in item.get("evidence_needed", [])[:4]],
                "maturity": item.get("maturity", ""),
            }
            for item in ideas.get("ideas", [])[:12]
            if isinstance(item, dict)
        ]
    }


def summarize_research_critic(critic: dict[str, Any]) -> dict[str, Any]:
    return {
        "panel_summary": truncate_text(critic.get("panel_summary", ""), 600),
        "reviews": [
            {
                "idea_id": item.get("idea_id", ""),
                "overall_decision": item.get("overall_decision", ""),
                "current_weaknesses": [truncate_text(weakness, 160) for weakness in item.get("current_weaknesses", [])[:4]],
                "upgrade_opportunities": [
                    truncate_text(opportunity, 160) for opportunity in item.get("upgrade_opportunities", [])[:4]
                ],
                "better_framing": truncate_text(item.get("better_framing", ""), 220),
            }
            for item in critic.get("reviews", [])[:12]
            if isinstance(item, dict)
        ],
        "hard_reject_ids": critic.get("hard_reject_ids", [])[:12],
    }


def compact_research_critic(critic: dict[str, Any]) -> dict[str, Any]:
    return {
        "panel_summary": truncate_text(critic.get("panel_summary", ""), 700),
        "reviews": [
            {
                "idea_id": item.get("idea_id", ""),
                "overall_decision": item.get("overall_decision", ""),
                "private_scores": item.get("private_scores", {}),
                "current_weaknesses": [truncate_text(weakness, 180) for weakness in item.get("current_weaknesses", [])[:5]],
                "repairable_potential": truncate_text(item.get("repairable_potential", ""), 260),
                "irrecoverable_flaws": [truncate_text(flaw, 180) for flaw in item.get("irrecoverable_flaws", [])[:4]],
                "upgrade_opportunities": [
                    truncate_text(opportunity, 200) for opportunity in item.get("upgrade_opportunities", [])[:5]
                ],
                "better_framing": truncate_text(item.get("better_framing", ""), 260),
                "stronger_mechanism_options": [
                    truncate_text(option, 180) for option in item.get("stronger_mechanism_options", [])[:4]
                ],
                "missing_evidence": [truncate_text(evidence, 140) for evidence in item.get("missing_evidence", [])[:4]],
            }
            for item in critic.get("reviews", [])[:12]
            if isinstance(item, dict)
        ],
        "hard_reject_ids": critic.get("hard_reject_ids", [])[:12],
    }


def summarize_revised_research_ideas(revised: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "revision_strategy": item.get("revision_strategy", ""),
            "central_insight": truncate_text(item.get("central_insight", ""), 260),
            "technical_move": truncate_text(item.get("technical_move", ""), 220),
            "minimum_discriminating_experiment": truncate_text(item.get("minimum_discriminating_experiment", ""), 220),
        }
        for item in revised.get("revised_ideas", [])[:12]
        if isinstance(item, dict)
    ]


def compact_revised_research_ideas(revised: dict[str, Any]) -> dict[str, Any]:
    return {
        "revised_ideas": [
            {
                "id": item.get("id", ""),
                "source_idea_ids": item.get("source_idea_ids", [])[:5],
                "name": item.get("name", ""),
                "revision_strategy": item.get("revision_strategy", ""),
                "critic_issues_addressed": [
                    truncate_text(issue, 160) for issue in item.get("critic_issues_addressed", [])[:5]
                ],
                "central_insight": truncate_text(item.get("central_insight", ""), 360),
                "problem_framing": truncate_text(item.get("problem_framing", ""), 300),
                "nontrivial_mechanism_match": truncate_text(item.get("nontrivial_mechanism_match", ""), 320),
                "technical_move": truncate_text(item.get("technical_move", ""), 320),
                "novelty_boundary": truncate_text(item.get("novelty_boundary", ""), 300),
                "stronger_baseline_to_beat": truncate_text(item.get("stronger_baseline_to_beat", ""), 240),
                "minimum_discriminating_experiment": truncate_text(item.get("minimum_discriminating_experiment", ""), 300),
                "falsifiable_prediction": truncate_text(item.get("falsifiable_prediction", ""), 240),
                "failure_value": truncate_text(item.get("failure_value", ""), 240),
                "main_risks": [truncate_text(risk, 160) for risk in item.get("main_risks", [])[:4]],
                "evidence_needed": [truncate_text(evidence, 140) for evidence in item.get("evidence_needed", [])[:4]],
                "maturity": item.get("maturity", ""),
            }
            for item in revised.get("revised_ideas", [])[:12]
            if isinstance(item, dict)
        ],
        "discarded": revised.get("discarded", [])[:12],
    }


def truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def extract_bottlenecks(config: dict[str, Any], trace: TraceLogger, context: dict[str, Any]) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages("bottleneck_extractor", {"schema": BOTTLENECK_SCHEMA, "context": context}),
        trace=trace,
        stage="bottleneck_extractor",
        validator=normalize_bottlenecks,
        temperature=0.2,
        timeout=180,
    )


def map_mechanism_transfers(config: dict[str, Any], trace: TraceLogger, context: dict[str, Any], bottlenecks: dict[str, Any]) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "mechanism_transfer_mapper",
            {"schema": MECHANISM_TRANSFER_SCHEMA, "context": context, "bottlenecks": bottlenecks},
        ),
        trace=trace,
        stage="mechanism_transfer_mapper",
        validator=normalize_mechanism_transfers,
        temperature=0.35,
        timeout=180,
    )


def generate_idea_branches(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    bottlenecks: dict[str, Any],
    transfers: dict[str, Any],
    branch_count: int,
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "idea_branch_generator",
            {
                "schema": IDEA_BRANCH_SCHEMA,
                "branch_count": branch_count,
                "context": context,
                "bottlenecks": bottlenecks,
                "mechanism_transfers": transfers,
            },
        ),
        trace=trace,
        stage="idea_branch_generator",
        validator=normalize_idea_branches,
        temperature=0.55,
        timeout=240,
    )


def screen_idea_branches(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    branches_doc: dict[str, Any],
    shortlist_count: int,
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "branch_screener",
            {
                "schema": BRANCH_SCREEN_SCHEMA,
                "shortlist_count": shortlist_count,
                "context": context,
                "branches": compact_branches_for_prompt(branches_doc),
            },
        ),
        trace=trace,
        stage="branch_screener",
        validator=normalize_branch_screen,
        temperature=0.15,
        timeout=180,
    )


def strengthen_ideas(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    branches_doc: dict[str, Any],
    screen: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "idea_strengthener",
            {
                "schema": STRENGTHENED_IDEAS_SCHEMA,
                "context": context,
                "branches": compact_branches_for_prompt(branches_doc),
                "screen": compact_screen_for_prompt(screen),
            },
        ),
        trace=trace,
        stage="idea_strengthener",
        validator=normalize_strengthened_ideas,
        temperature=0.35,
        timeout=180,
    )


def decide_ideas(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    bottlenecks: dict[str, Any],
    transfers: dict[str, Any],
    branches_doc: dict[str, Any],
    screen: dict[str, Any],
    strengthened: dict[str, Any],
    final_count: int,
) -> dict[str, Any]:
    return call_json(
        config,
        "frontier",
        prompt_messages(
            "decision_chair",
            {
                "schema": IDEA_SEARCH_RESULT_SCHEMA,
                "final_count": final_count,
                "context": context,
                "bottlenecks": {"bottlenecks": summarize_bottlenecks(bottlenecks)},
                "mechanism_transfers": {"transfers": summarize_transfers(transfers)},
                "branches": compact_branches_for_prompt(branches_doc),
                "screen": compact_screen_for_prompt(screen),
                "strengthened_ideas": compact_strengthened_for_prompt(strengthened),
            },
        ),
        trace=trace,
        stage="decision_chair",
        validator=normalize_idea_search_result,
        temperature=0.1,
        timeout=240,
    )


def mine_research_opportunities(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    shared: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "research_opportunity_miner",
            {
                "schema": RESEARCH_OPPORTUNITY_SCHEMA,
                "context": context,
                **shared,
            },
        ),
        trace=trace,
        stage="research_opportunity_miner",
        validator=normalize_research_opportunities,
        temperature=0.25,
        timeout=float(config.get("research_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def build_research_ideas(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    opportunities: dict[str, Any],
    idea_count: int,
    shared: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "research_builder",
            {
                "schema": RESEARCH_IDEA_SCHEMA,
                "idea_count": idea_count,
                "context": context,
                "opportunities": compact_research_opportunities(opportunities),
                **shared,
            },
        ),
        trace=trace,
        stage="research_builder",
        validator=normalize_research_ideas,
        temperature=0.45,
        timeout=float(config.get("research_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def critique_research_ideas(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    ideas: dict[str, Any],
    shared: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "frontier",
        prompt_messages(
            "research_critic_panel",
            {
                "schema": RESEARCH_CRITIC_SCHEMA,
                "context": context,
                "ideas": compact_research_ideas(ideas),
                **shared,
            },
        ),
        trace=trace,
        stage="research_critic_panel",
        validator=normalize_research_critic,
        temperature=0.15,
        timeout=float(config.get("research_critic_timeout_sec", config.get("llm_timeout_sec", 300))),
    )


def revise_research_ideas(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    ideas: dict[str, Any],
    critic: dict[str, Any],
    shared: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "strong",
        prompt_messages(
            "research_reviser",
            {
                "schema": RESEARCH_REVISION_SCHEMA,
                "context": context,
                "ideas": compact_research_ideas(ideas),
                "critic_panel": compact_research_critic(critic),
                **shared,
            },
        ),
        trace=trace,
        stage="research_reviser",
        validator=normalize_research_revision,
        temperature=0.35,
        timeout=float(config.get("research_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def decide_research(
    config: dict[str, Any],
    trace: TraceLogger,
    context: dict[str, Any],
    opportunities: dict[str, Any],
    critic: dict[str, Any],
    revised: dict[str, Any],
    final_count: int,
    shared: dict[str, Any],
) -> dict[str, Any]:
    return call_json(
        config,
        "frontier",
        prompt_messages(
            "research_chair",
            {
                "schema": RESEARCH_DECISION_SCHEMA,
                "final_count": final_count,
                "context": context,
                "opportunities": compact_research_opportunities(opportunities),
                "critic_panel": compact_research_critic(critic),
                "revised_ideas": compact_revised_research_ideas(revised),
                **shared,
            },
        ),
        trace=trace,
        stage="research_chair",
        validator=normalize_research_decision,
        temperature=0.1,
        timeout=float(config.get("research_chair_timeout_sec", config.get("llm_timeout_sec", 300))),
    )


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
    project: IdeaProject,
    brief: dict[str, Any],
    claims: dict[str, Any],
    papers: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
    progress=None,
) -> dict[str, Any]:
    if not papers:
        fallback = build_novelty_matrix(claims_to_decomposition(brief, claims), [], config)
        fallback["overall_recommendation"] = "proceed_with_caution"
        return fallback

    stage_dir = project.state_dir / "run_deep_stages"
    stage_dir.mkdir(parents=True, exist_ok=True)
    literature_store = build_or_load_literature_store(
        project,
        papers=papers,
        brief=brief,
        claims=claims,
        novelty_matrix={},
        reviewer_report={},
        evidence_qa=evidence or {},
        progress=progress,
    )
    claim_items = [item for item in claims.get("claims", []) if isinstance(item, dict)]
    if not claim_items:
        claim_items = [{"id": "C1", "claim": brief.get("problem_statement", "") or brief.get("topic", "")}]

    batch_size = max(1, int(config.get("novelty_matrix_claim_batch_size", 1)))
    batches: list[dict[str, Any]] = []
    for batch_index, claim_batch in enumerate(chunk_list(claim_items, batch_size), start=1):
        batch_path = stage_dir / f"novelty_matrix_v2_batch_{batch_index}.json"
        batch = cached_json_stage(
            stage_dir,
            f"novelty_matrix_v2_batch_{batch_index}",
            batch_path,
            {
                "stage_version": 2,
                "brief": brief,
                "claims": claims,
                "claim_batch": claim_batch,
                "papers": papers_for_cache(papers),
                "evidence": evidence_for_cache(evidence or {}),
            },
            lambda claim_batch=claim_batch, batch_index=batch_index: build_llm_matrix_batch(
                config,
                trace,
                brief,
                claims,
                literature_store,
                claim_batch,
                batch_index,
                evidence or {},
            ),
            accept=lambda value: isinstance(value, dict) and bool(value.get("rows")),
            progress=progress,
            label=f"novelty matrix batch {batch_index} [strong]",
        )
        batches.append(batch)

    matrix = merge_novelty_matrix_batches(batches)
    write_json(stage_dir / "novelty_matrix_v2_merged.json", matrix)
    return matrix


def build_llm_matrix_batch(
    config: dict[str, Any],
    trace: TraceLogger,
    brief: dict[str, Any],
    claims: dict[str, Any],
    literature_store: dict[str, Any],
    claim_batch: list[dict[str, Any]],
    batch_index: int,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    base_context = {
        "brief": brief,
        "claims": claims,
        "novelty_matrix": {},
        "reviewer_report": {},
    }
    evidence_contexts = [
        retrieve_novelty_claim_context(
            store=literature_store,
            base_context=base_context,
            claim=claim,
            evidence_qa=evidence,
        )
        for claim in claim_batch
    ]
    return call_json(
        config,
        "strong",
        prompt_messages(
            "novelty_matrix_builder",
            {
                "schema": NOVELTY_SCHEMA,
                "batch_index": batch_index,
                "brief": brief,
                "claims": {
                    "claims": claim_batch,
                    "risk_questions": claims.get("risk_questions", [])[:8],
                },
                "evidence_contexts": evidence_contexts,
                "evidence_qa": {
                    "status": evidence.get("status", ""),
                    "reason": evidence.get("reason", ""),
                },
            },
        ),
        trace=trace,
        stage=f"novelty_matrix_builder_batch_{batch_index}",
        validator=normalize_matrix,
        temperature=0.1,
        timeout=float(config.get("novelty_matrix_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def merge_novelty_matrix_batches(batches: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    recommendations: list[str] = []
    warnings: list[str] = []
    for batch in batches:
        warning = str(batch.get("warning", "")).strip()
        if warning and warning not in warnings:
            warnings.append(warning)
        recommendation = str(batch.get("overall_recommendation", "")).strip()
        if recommendation:
            recommendations.append(recommendation)
        for row in batch.get("rows", []):
            if not isinstance(row, dict):
                continue
            claim_id = str(row.get("claim_id", "")).strip()
            key = claim_id or str(row.get("claim", "")).strip().lower()
            if key and key in seen_claims:
                continue
            if key:
                seen_claims.add(key)
            rows.append(row)
    return {
        "warning": warnings[0] if warnings else "Batched evidence-grounded novelty matrix; this is not a novelty proof.",
        "rows": rows,
        "overall_recommendation": strongest_recommendation(recommendations),
    }


def strongest_recommendation(values: list[str]) -> str:
    order = {
        "proceed": 0,
        "proceed_with_caution": 1,
        "pivot": 2,
        "abandon": 3,
    }
    if not values:
        return "proceed_with_caution"
    return max(values, key=lambda item: order.get(item, 1))


def chunk_list(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def compact_brief_for_run_deep(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": brief.get("topic", ""),
        "problem_statement": truncate_text(brief.get("problem_statement", ""), 1200),
        "domain": brief.get("domain", [])[:10],
        "known_context": brief.get("known_context", [])[:10],
        "constraints": brief.get("constraints", [])[:8],
        "success_criteria": brief.get("success_criteria", [])[:8],
        "uncertainties": brief.get("uncertainties", [])[:8],
    }


def compact_claims_for_review(claims: dict[str, Any]) -> dict[str, Any]:
    compact_claims = []
    for claim in claims.get("claims", [])[:16]:
        if not isinstance(claim, dict):
            continue
        compact_claims.append(
            {
                "id": claim.get("id", ""),
                "type": claim.get("type", ""),
                "claim": truncate_text(claim.get("claim", ""), 700),
                "mechanism": truncate_text(claim.get("mechanism", ""), 500),
                "task_context": truncate_text(claim.get("task_context", ""), 400),
                "risk_if_false": truncate_text(claim.get("risk_if_false", ""), 500),
                "equivalent_terms": claim.get("equivalent_terms", [])[:8],
                "search_priority": claim.get("search_priority", ""),
            }
        )
    return {
        "claims": compact_claims,
        "risk_questions": claims.get("risk_questions", [])[:12],
    }


def compact_matrix_for_review(matrix: dict[str, Any], *, closest_limit: int = 4) -> dict[str, Any]:
    rows = []
    for row in matrix.get("rows", [])[:16]:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "claim_id": row.get("claim_id", ""),
                "claim": truncate_text(row.get("claim", ""), 700),
                "risk": row.get("risk", ""),
                "closest_papers": [
                    {
                        "title": paper.get("title", ""),
                        "year": paper.get("year", ""),
                        "url": paper.get("url", ""),
                        "overlap": truncate_text(paper.get("overlap", ""), 450),
                        "difference": truncate_text(paper.get("difference", ""), 450),
                        "evidence_strength": paper.get("evidence_strength", ""),
                    }
                    for paper in row.get("closest_papers", [])[:closest_limit]
                    if isinstance(paper, dict)
                ],
                "missing_evidence": [truncate_text(item, 350) for item in row.get("missing_evidence", [])[:6]],
                "positioning": truncate_text(row.get("positioning", ""), 700),
            }
        )
    return {
        "warning": matrix.get("warning", ""),
        "overall_recommendation": matrix.get("overall_recommendation", ""),
        "rows": rows,
    }


def compact_matrix_for_refinement(matrix: dict[str, Any]) -> dict[str, Any]:
    return compact_matrix_for_review(matrix, closest_limit=3)


def compact_review_for_refinement(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": truncate_text(review.get("summary", ""), 1000),
        "score": review.get("score", ""),
        "recommendation": review.get("recommendation", ""),
        "strongest_objections": [truncate_text(item, 450) for item in review.get("strongest_objections", [])[:8]],
        "minimum_fixes": [truncate_text(item, 450) for item in review.get("minimum_fixes", [])[:8]],
        "reviewer_likely_prior_work_attack": [
            truncate_text(item, 450) for item in review.get("reviewer_likely_prior_work_attack", [])[:8]
        ],
        "experiment_concerns": [truncate_text(item, 450) for item in review.get("experiment_concerns", [])[:8]],
        "positioning_advice": truncate_text(review.get("positioning_advice", ""), 1000),
    }


def compact_ideas_for_experiment(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    idea_items = [idea for idea in ideas if isinstance(idea, dict)]
    for idea in sorted(idea_items, key=lambda item: item.get("rank", 999))[:8]:
        compact.append(
            {
                "rank": idea.get("rank", ""),
                "name": idea.get("name", ""),
                "research_question": truncate_text(idea.get("research_question", ""), 600),
                "method": [truncate_text(item, 320) for item in idea.get("method", [])[:4]],
                "novelty_lever": truncate_text(idea.get("novelty_lever", ""), 500),
                "minimum_experiment": truncate_text(idea.get("minimum_experiment", ""), 600),
                "main_risk": truncate_text(idea.get("main_risk", ""), 500),
                "expected_contribution": idea.get("expected_contribution", ""),
            }
        )
    return compact


def review_project(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], claims: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": REVIEW_SCHEMA,
        "brief": compact_brief_for_run_deep(brief),
        "claims": compact_claims_for_review(claims),
        "novelty_matrix": compact_matrix_for_review(matrix),
        "compression_note": (
            "Inputs are compressed from the full run-deep artifacts. Preserve the same adversarial review standard, "
            "but avoid treating omitted details as absent evidence."
        ),
    }
    return call_json(
        config,
        "frontier",
        prompt_messages("adversarial_reviewer", payload),
        trace=trace,
        stage="adversarial_reviewer",
        validator=normalize_review,
        temperature=0.1,
        timeout=float(config.get("review_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def refine_with_llm(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], matrix: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    payload = {
        "schema": IDEA_SCHEMA,
        "brief": compact_brief_for_run_deep(brief),
        "novelty_matrix": compact_matrix_for_refinement(matrix),
        "review": compact_review_for_refinement(review),
        "compression_note": (
            "The novelty matrix and review are compressed evidence summaries. Generate ideas that answer the strongest "
            "objections and explicitly preserve the novelty boundary."
        ),
    }
    return call_json(
        config,
        "strong",
        prompt_messages("idea_refiner", payload),
        trace=trace,
        stage="idea_refiner",
        validator=normalize_ideas,
        timeout=float(config.get("idea_refiner_timeout_sec", config.get("llm_timeout_sec", 240))),
    )


def plan_experiment_with_llm(config: dict[str, Any], trace: TraceLogger, brief: dict[str, Any], matrix: dict[str, Any], review: dict[str, Any], ideas: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema": EXPERIMENT_SCHEMA,
        "brief": compact_brief_for_run_deep(brief),
        "novelty_matrix": compact_matrix_for_refinement(matrix),
        "review": compact_review_for_refinement(review),
        "ideas": compact_ideas_for_experiment(ideas),
        "compression_note": (
            "Inputs are compressed from run-deep artifacts. Design experiments against the retained claim risks, "
            "closest prior-work attacks, and reviewer concerns."
        ),
    }
    return call_json(
        config,
        "standard",
        prompt_messages("experiment_planner", payload),
        trace=trace,
        stage="experiment_planner",
        validator=normalize_experiment,
        timeout=float(config.get("experiment_timeout_sec", config.get("llm_timeout_sec", 240))),
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
    path = detail_report_path(project, "run_deep_dry_run.md")
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


def write_idea_search_dry_run(
    project: IdeaProject,
    base_context: dict[str, Any],
    literature_store: dict[str, Any],
    params: dict[str, int],
) -> Path:
    trace = TraceLogger(project.traces_dir)
    placeholder_bottlenecks = "<bottleneck_extractor output>"
    placeholder_transfers = "<mechanism_transfer_mapper output>"
    placeholder_branches = "<batched idea_branch_generator output>"
    placeholder_screen = "<branch_screener output>"
    placeholder_strengthened = "<idea_strengthener output>"
    bottleneck_context = retrieve_stage_context(store=literature_store, stage="bottleneck_extractor", base_context=base_context)
    transfer_context = retrieve_stage_context(
        store=literature_store,
        stage="mechanism_transfer_mapper",
        base_context=base_context,
        extra_context={"bottleneck_summary": placeholder_bottlenecks},
    )
    branch_context = retrieve_stage_context(
        store=literature_store,
        stage="idea_branch_generator",
        base_context=base_context,
        extra_context={
            "bottleneck_summary": placeholder_bottlenecks,
            "transfer_summary": placeholder_transfers,
            "branch_batch": {
                "batch_index": 1,
                "batch_size": min(5, params["branches"]),
                "target_total_branches": params["branches"],
                "track_focus": "conservative and diagnostic branches",
                "existing_branches": [],
            },
        },
    )
    screen_context = retrieve_stage_context(
        store=literature_store,
        stage="branch_screener",
        base_context=base_context,
        extra_context={"branches": placeholder_branches},
        include_extra_context=False,
    )
    strengthener_context = retrieve_stage_context(
        store=literature_store,
        stage="idea_strengthener",
        base_context=base_context,
        extra_context={"branches": placeholder_branches, "screen_summary": placeholder_screen},
        include_extra_context=False,
    )
    decision_context = retrieve_stage_context(
        store=literature_store,
        stage="decision_chair",
        base_context=base_context,
        extra_context={
            "bottleneck_summary": placeholder_bottlenecks,
            "transfer_summary": placeholder_transfers,
            "screen_summary": placeholder_screen,
            "strengthened_summary": placeholder_strengthened,
        },
        include_extra_context=False,
    )
    payloads = {
        "bottleneck_extractor": prompt_messages("bottleneck_extractor", {"schema": BOTTLENECK_SCHEMA, "context": bottleneck_context}),
        "mechanism_transfer_mapper": prompt_messages(
            "mechanism_transfer_mapper",
            {"schema": MECHANISM_TRANSFER_SCHEMA, "context": transfer_context, "bottlenecks": placeholder_bottlenecks},
        ),
        "idea_branch_generator_batch_1": prompt_messages(
            "idea_branch_generator",
            {
                "schema": IDEA_BRANCH_SCHEMA,
                "branch_count": min(5, params["branches"]),
                "context": branch_context,
                "bottlenecks": placeholder_bottlenecks,
                "mechanism_transfers": placeholder_transfers,
            },
        ),
        "branch_screener": prompt_messages(
            "branch_screener",
            {
                "schema": BRANCH_SCREEN_SCHEMA,
                "shortlist_count": params["shortlist"],
                "context": screen_context,
                "branches": placeholder_branches,
            },
        ),
        "idea_strengthener": prompt_messages(
            "idea_strengthener",
            {
                "schema": STRENGTHENED_IDEAS_SCHEMA,
                "context": strengthener_context,
                "branches": placeholder_branches,
                "screen": placeholder_screen,
            },
        ),
        "decision_chair": prompt_messages(
            "decision_chair",
            {
                "schema": IDEA_SEARCH_RESULT_SCHEMA,
                "final_count": params["final"],
                "context": decision_context,
                "bottlenecks": placeholder_bottlenecks,
                "mechanism_transfers": placeholder_transfers,
                "branches": placeholder_branches,
                "screen": placeholder_screen,
                "strengthened_ideas": placeholder_strengthened,
            },
        ),
    }
    trace.write_artifact("idea_search_dry_run_prompts", "json", payloads)
    path = detail_report_path(project, "idea_search_dry_run.md")
    write_text(
        path,
        "# idea-search dry run\n\n"
        "LLM was not called. Prompt payloads were written to `traces/idea_search_dry_run_prompts.json`.\n\n"
        f"- branches: {params['branches']}\n"
        f"- shortlist: {params['shortlist']}\n"
        f"- final: {params['final']}\n",
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


def render_idea_search(result: dict[str, Any]) -> str:
    final_result = result.get("final", {})
    params = result.get("parameters", {})
    store = result.get("literature_store", {})
    lines = [
        "# Idea Search Report",
        "",
        f"- Branches: {params.get('branches', '')}",
        f"- Shortlist: {params.get('shortlist', '')}",
        f"- Final: {params.get('final', '')}",
        f"- Literature store papers: {store.get('papers', '')}",
        f"- Literature store PDF passages: {store.get('passages', '')}",
        f"- Literature store evidence items: {store.get('evidence_items', '')}",
        "",
        "## Summary",
        "",
        final_result.get("summary", ""),
        "",
        "## Bottlenecks",
        "",
    ]
    bottleneck_rows = [
        [
            item.get("id", ""),
            item.get("description", ""),
            item.get("failure_mode", ""),
            item.get("evidence_signal", ""),
        ]
        for item in result.get("bottlenecks", {}).get("bottlenecks", [])
    ]
    lines.append(md_table(["id", "bottleneck", "failure mode", "evidence signal"], bottleneck_rows) if bottleneck_rows else "暂无。")

    lines.extend(["", "## Mechanism Transfers", ""])
    transfer_rows = [
        [
            item.get("id", ""),
            item.get("source_field", ""),
            item.get("source_mechanism", ""),
            item.get("target_bottleneck", ""),
            item.get("minimum_test", ""),
        ]
        for item in result.get("mechanism_transfers", {}).get("transfers", [])
    ]
    lines.append(md_table(["id", "source", "mechanism", "target bottleneck", "minimum test"], transfer_rows) if transfer_rows else "暂无。")

    lines.extend(["", "## Generated Branches", ""])
    branch_rows = [
        [
            item.get("id", ""),
            item.get("track", ""),
            item.get("name", ""),
            item.get("novelty_hypothesis", ""),
            item.get("minimum_experiment", ""),
        ]
        for item in result.get("branches", {}).get("branches", [])
    ]
    lines.append(md_table(["id", "track", "name", "novelty hypothesis", "minimum experiment"], branch_rows) if branch_rows else "暂无。")

    lines.extend(["", "## Shortlist", ""])
    shortlist_rows = [
        [
            item.get("branch_id", ""),
            item.get("decision", ""),
            item.get("score", ""),
            item.get("rationale", ""),
            item.get("salvage_path", ""),
        ]
        for item in result.get("screen", {}).get("shortlist", [])
    ]
    lines.append(md_table(["branch", "decision", "score", "rationale", "salvage"], shortlist_rows) if shortlist_rows else "暂无。")

    lines.extend(["", "## Final Ideas", ""])
    for idea in sorted(final_result.get("final_ideas", []), key=lambda item: item.get("rank", 999)):
        lines.extend(
            [
                f"### {idea.get('rank', '')}. {idea.get('name', '')}",
                "",
                f"- Decision: {idea.get('decision', '')}",
                f"- Branch: {idea.get('branch_id', '')}",
                f"- Research question: {idea.get('research_question', '')}",
                f"- Technical move: {idea.get('technical_move', '')}",
                f"- Why now: {idea.get('why_now', '')}",
                f"- Novelty lever: {idea.get('novelty_lever', '')}",
                f"- Closest prior-work attack: {idea.get('closest_prior_work_attack', '')}",
                f"- Minimum experiment: {idea.get('minimum_experiment', '')}",
                f"- Falsifiable prediction: {idea.get('falsifiable_prediction', '')}",
                "",
                "Failure conditions:",
            ]
        )
        for item in idea.get("failure_conditions", []):
            lines.append(f"- {item}")
        lines.extend(["", "Evidence needed:"])
        for item in idea.get("evidence_needed", []):
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["## Global Risks", ""])
    for item in final_result.get("global_risks", []):
        lines.append(f"- {item}")
    if not final_result.get("global_risks"):
        lines.append("暂无。")
    return "\n".join(lines).strip() + "\n"


def render_research(result: dict[str, Any]) -> str:
    final_result = result.get("final", {})
    params = result.get("parameters", {})
    store = result.get("literature_store", {})
    lines = [
        "# Research Proposal 报告",
        "",
        "这个文件是主报告：它把闭环 Builder / Critic / Reviser / Chair 的结果整理成可直接阅读的研究方案。内部候选编号只保留在 `reports/details/research_rounds.md`，不作为最终排名或方案名称。",
        "",
        "## 运行摘要",
        "",
        f"- 初始候选数：{params.get('ideas', '')}",
        f"- 最终选择数：{params.get('final', '')}",
        f"- 文献数：{store.get('papers', '')}",
        f"- PDF passages：{store.get('passages', '')}",
        f"- evidence items：{store.get('evidence_items', '')}",
        "",
        "## 总体结论",
        "",
        final_result.get("summary", ""),
        "",
        "## 最终研究方案",
        "",
    ]
    final_ideas = sorted(final_result.get("final_ideas", []), key=lambda item: item.get("rank", 999))
    if not final_ideas:
        lines.append("暂无。")
    for idea in final_ideas:
        evidence_basis = idea.get("evidence_basis", [])
        open_assumptions = idea.get("open_assumptions", [])
        lines.extend(
            [
                f"### 方案 {idea.get('rank', '')}：{idea.get('name', '')}",
                "",
                f"- 决策：{idea.get('decision', '')}",
                f"- 方案概览：{idea.get('proposal_summary') or idea.get('why_selected', '')}",
                f"- 研究问题：{idea.get('research_question', '')}",
                f"- 目标问题：{idea.get('target_problem', '')}",
                "",
                "#### 核心 Idea",
                "",
                f"- central insight：{idea.get('central_insight', '')}",
                f"- proposed method：{idea.get('proposed_method', '')}",
                f"- mechanism design：{idea.get('mechanism_design', '')}",
                f"- training / optimization signal：{idea.get('training_signal', '')}",
                f"- expected contribution：{idea.get('expected_contribution', '')}",
                f"- 相比初版的进步：{idea.get('why_this_is_better_than_initial_version', '')}",
                "",
                "#### 边界与实验",
                "",
                f"- novelty boundary：{idea.get('novelty_boundary', '')}",
                f"- stronger baseline：{idea.get('stronger_baseline_to_beat', '')}",
                f"- evaluation protocol：{idea.get('evaluation_protocol', '')}",
                f"- 最小区分实验：{idea.get('minimum_discriminating_experiment', '')}",
                "",
                "#### 证据基础与待验证假设",
            ]
        )
        if evidence_basis:
            lines.append("")
            lines.append("证据基础：")
            for item in evidence_basis:
                lines.append(f"- {item}")
        else:
            lines.append("- 证据基础：暂无明确证据条目。")
        lines.append("")
        if open_assumptions:
            lines.append("待验证假设：")
            for item in open_assumptions:
                lines.append(f"- {item}")
        else:
            lines.append("- 待验证假设：暂无。")
        lines.extend(["", "#### 风险与失败条件", ""])
        for item in idea.get("failure_conditions", []):
            lines.append(f"- {item}")
        if not idea.get("failure_conditions"):
            lines.append("- 暂无。")
        lines.extend(["", "#### 下一步检查", "", "文献检查："])
        for item in idea.get("next_literature_checks", []):
            lines.append(f"- {item}")
        if not idea.get("next_literature_checks"):
            lines.append("- 暂无。")
        lines.extend(["", "实验检查："])
        for item in idea.get("next_experiment_checks", []):
            lines.append(f"- {item}")
        if not idea.get("next_experiment_checks"):
            lines.append("- 暂无。")
        lines.append("")

    lines.extend(["## 可保留 Pivot", ""])
    for item in final_result.get("promising_pivots", []):
        lines.append(f"- {item}")
    if not final_result.get("promising_pivots"):
        lines.append("暂无。")

    lines.extend(["", "## 全局风险", ""])
    for item in final_result.get("global_risks", []):
        lines.append(f"- {item}")
    if not final_result.get("global_risks"):
        lines.append("暂无。")
    return "\n".join(lines).strip() + "\n"


def render_research_rounds(result: dict[str, Any]) -> str:
    lines = [
        "# Research Workflow 生成过程详情",
        "",
        "这里保留内部候选编号和每轮审查记录，用于追踪最终方案从哪里来。主报告请看 `reports/research.md`。",
        "",
        "## Opportunities",
        "",
    ]
    opportunity_rows = [
        [
            item.get("id", ""),
            item.get("novelty_path", ""),
            item.get("bottleneck", ""),
            item.get("why_important", ""),
            item.get("risk", ""),
        ]
        for item in result.get("opportunities", {}).get("bottleneck_opportunities", [])
    ]
    lines.append(md_table(["id", "path", "bottleneck", "why important", "risk"], opportunity_rows) if opportunity_rows else "暂无。")

    lines.extend(["", "## Initial Ideas", ""])
    initial_rows = [
        [
            item.get("id", ""),
            item.get("name", ""),
            item.get("central_insight", ""),
            item.get("stronger_baseline_to_beat", ""),
            item.get("maturity", ""),
        ]
        for item in result.get("initial_ideas", {}).get("ideas", [])
    ]
    lines.append(md_table(["id", "name", "central insight", "stronger baseline", "maturity"], initial_rows) if initial_rows else "暂无。")

    lines.extend(["", "## Critic Panel", ""])
    critic = result.get("critic_panel", {})
    lines.extend(["", critic.get("panel_summary", ""), ""])
    critic_rows = [
        [
            item.get("idea_id", ""),
            item.get("overall_decision", ""),
            "; ".join(item.get("current_weaknesses", [])[:3]),
            "; ".join(item.get("upgrade_opportunities", [])[:3]),
            item.get("better_framing", ""),
        ]
        for item in critic.get("reviews", [])
    ]
    lines.append(md_table(["idea", "decision", "weaknesses", "upgrade opportunities", "better framing"], critic_rows) if critic_rows else "暂无。")

    lines.extend(["", "## Revised Ideas", ""])
    revised_rows = [
        [
            item.get("id", ""),
            item.get("revision_strategy", ""),
            item.get("name", ""),
            item.get("central_insight", ""),
            item.get("minimum_discriminating_experiment", ""),
        ]
        for item in result.get("revised_ideas", {}).get("revised_ideas", [])
    ]
    lines.append(md_table(["id", "strategy", "name", "central insight", "minimum experiment"], revised_rows) if revised_rows else "暂无。")
    return "\n".join(lines).strip() + "\n"

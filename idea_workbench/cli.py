from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .heuristics import (
    build_experiment_plan,
    build_novelty_matrix,
    decompose_seed,
    generate_queries,
    refine_ideas,
)
from .llm_workflow import (
    doctor_report,
    render_doctor,
    run_deep,
    run_evidence,
    run_literature,
    run_review,
)
from .pdfs import run_pdf_fetch
from .project import (
    assert_project,
    init_project,
    load_config,
    read_json,
    read_text,
    write_json,
    write_text,
)
from .render import (
    render_decomposition,
    render_experiment_plan,
    render_final_report,
    render_matrix,
    render_queries,
    render_refined_ideas,
    render_search_log,
)
from .search import run_search


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - make CLI failure readable.
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idea-workbench",
        description="Local CLI for literature-grounded research idea refinement.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check GPT-compatible API and literature backend configuration")
    doctor.add_argument("project", nargs="?", help="optional idea project directory")
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="create a new idea project")
    init.add_argument("project", help="project directory")
    init.add_argument("--force", action="store_true", help="overwrite seed/config/query files if they already exist")
    init.add_argument("--seed-text", help="write this text into seed.md")
    init.set_defaults(func=cmd_init)

    decompose = sub.add_parser("decompose", help="decompose seed.md into searchable claims")
    decompose.add_argument("project")
    decompose.set_defaults(func=cmd_decompose)

    search = sub.add_parser("search", help="generate queries and optionally search public paper APIs")
    search.add_argument("project")
    search.add_argument("--offline", action="store_true", help="only generate queries; do not call paper APIs")
    search.add_argument("--limit", type=int, default=None, help="max papers per query per source")
    search.add_argument("--sources", default=None, help="comma-separated sources: arxiv,openalex,semantic_scholar")
    search.set_defaults(func=cmd_search)

    literature = sub.add_parser("literature", help="run literature retrieval using generated queries")
    literature.add_argument("project")
    literature.add_argument("--offline", action="store_true", help="only write query/search report; do not call paper APIs")
    literature.add_argument("--limit", type=int, default=None, help="max papers per query per source")
    literature.add_argument("--sources", default=None, help="comma-separated sources")
    literature.set_defaults(func=cmd_literature)

    evidence = sub.add_parser("evidence", help="run optional PDF evidence QA for claims and papers")
    evidence.add_argument("project")
    evidence.add_argument("--mock", action="store_true", help="use mock evidence QA for validation/tests")
    evidence.set_defaults(func=cmd_evidence)

    pdfs = sub.add_parser("pdfs", help="resolve and download PDFs for top retrieved papers")
    pdfs.add_argument("project")
    pdfs.add_argument("--top", type=int, default=10, help="number of top papers to consider")
    pdfs.add_argument("--dry-run", action="store_true", help="resolve PDF URLs and write reports without downloading")
    pdfs.add_argument("--force", action="store_true", help="redownload existing PDFs")
    pdfs.set_defaults(func=cmd_pdfs)

    matrix = sub.add_parser("matrix", help="build novelty matrix from claims and papers")
    matrix.add_argument("project")
    matrix.set_defaults(func=cmd_matrix)

    refine = sub.add_parser("refine", help="generate refined idea variants")
    refine.add_argument("project")
    refine.set_defaults(func=cmd_refine)

    experiment = sub.add_parser("experiment-plan", help="generate a minimal experiment plan")
    experiment.add_argument("project")
    experiment.set_defaults(func=cmd_experiment_plan)

    report = sub.add_parser("report", help="compose final Chinese report")
    report.add_argument("project")
    report.set_defaults(func=cmd_report)

    review = sub.add_parser("review", help="run frontier adversarial review on existing deep-run artifacts")
    review.add_argument("project")
    review.add_argument("--dry-run", action="store_true", help="write prompts without calling the LLM")
    review.set_defaults(func=cmd_review)

    run_deep_parser = sub.add_parser("run-deep", help="run the LLM-first deep research idea workflow")
    run_deep_parser.add_argument("project")
    run_deep_parser.add_argument("--dry-run", action="store_true", help="write prompts and required env notes without calling the LLM")
    run_deep_parser.add_argument("--offline-search", action="store_true", help="skip paper API calls after LLM query planning")
    run_deep_parser.add_argument("--limit", type=int, default=None, help="max papers per query per source")
    run_deep_parser.add_argument("--sources", default=None, help="comma-separated literature sources")
    run_deep_parser.set_defaults(func=cmd_run_deep)

    run_all = sub.add_parser("run-all", help="run decompose, search, matrix, refine, experiment-plan, report")
    run_all.add_argument("project")
    run_all.add_argument("--offline", action="store_true", help="only generate queries; do not call paper APIs")
    run_all.add_argument("--limit", type=int, default=None, help="max papers per query per source")
    run_all.add_argument("--sources", default=None, help="comma-separated sources: arxiv,openalex,semantic_scholar")
    run_all.set_defaults(func=cmd_run_all)

    return parser


def cmd_doctor(args: argparse.Namespace) -> int:
    project = assert_project(args.project) if args.project else None
    report = doctor_report(project)
    print(render_doctor(report))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    project = init_project(args.project, force=args.force)
    if args.seed_text:
        write_text(project.seed_path, "# 原始想法\n\n" + args.seed_text.strip() + "\n")
    print(f"created idea project: {project.root}")
    print(f"edit seed file: {project.seed_path}")
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    decomposition = ensure_decomposition(project, refresh=True)
    print(f"wrote {project.reports_dir / 'decomposition.md'}")
    print(f"claims: {len(decomposition.get('claims', []))}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    config = load_config(project)
    decomposition = ensure_decomposition(project, refresh=False)
    existing_queries = parse_query_file(read_text(project.queries_path))
    queries = existing_queries or generate_queries(decomposition, config)
    write_text(project.queries_path, render_queries(queries))
    write_json(project.state_dir / "queries.json", queries)

    sources = parse_sources(args.sources, config)
    limit = args.limit or int(config.get("max_results_per_query", 5))
    papers, errors = run_search(queries, sources=sources, limit=limit, offline=args.offline)
    write_json(project.papers_dir / "api_papers.json", papers)
    write_json(project.logs_dir / "search_errors.json", errors)
    write_text(project.reports_dir / "search_log.md", render_search_log(queries, papers, errors))

    print(f"wrote {project.queries_path}")
    print(f"papers: {len(papers)}")
    print(f"errors/notes: {len(errors)}")
    return 0


def cmd_literature(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    path = run_literature(
        project,
        offline=args.offline,
        limit=args.limit,
        sources=parse_sources_arg(args.sources),
    )
    print(f"wrote {path}")
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    path = run_evidence(project, mock=True if args.mock else None)
    print(f"wrote {path}")
    return 0


def cmd_pdfs(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    result = run_pdf_fetch(project, top=args.top, dry_run=args.dry_run, force=args.force)
    statuses: dict[str, int] = {}
    for paper in result.papers:
        status = str(paper.get("pdf_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    summary = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())) or "none"
    print(f"wrote {result.index_path}")
    print(f"wrote {result.report_path}")
    print(f"papers: {len(result.papers)}; {summary}")
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    matrix = ensure_matrix(project, refresh=True)
    print(f"wrote {project.reports_dir / 'novelty_matrix.md'}")
    print(f"claims: {len(matrix.get('rows', []))}")
    return 0


def cmd_refine(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    ideas = ensure_refined_ideas(project, refresh=True)
    print(f"wrote {project.reports_dir / 'refined_ideas.md'}")
    print(f"ideas: {len(ideas)}")
    return 0


def cmd_experiment_plan(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    plan = ensure_experiment_plan(project, refresh=True)
    print(f"wrote {project.reports_dir / 'experiment_plan.md'}")
    print(f"phases: {len(plan.get('phases', []))}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    final_path = ensure_final_report(project)
    print(f"wrote {final_path}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    path = run_review(project, dry_run=args.dry_run)
    print(f"wrote {path}")
    return 0


def cmd_run_deep(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    final_path = run_deep(
        project,
        dry_run=args.dry_run,
        offline_search=args.offline_search,
        limit=args.limit,
        sources=parse_sources_arg(args.sources),
    )
    print(f"wrote {final_path}")
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    project = assert_project(args.project)
    ensure_decomposition(project, refresh=True)
    search_args = argparse.Namespace(
        project=str(project.root),
        offline=args.offline,
        limit=args.limit,
        sources=args.sources,
    )
    cmd_search(search_args)
    ensure_matrix(project, refresh=True)
    ensure_refined_ideas(project, refresh=True)
    ensure_experiment_plan(project, refresh=True)
    final_path = ensure_final_report(project)
    print(f"complete: {final_path}")
    return 0


def ensure_decomposition(project: Any, *, refresh: bool) -> dict[str, Any]:
    state_path = project.state_dir / "decomposition.json"
    if state_path.exists() and not refresh:
        return read_json(state_path, {})
    seed_text = read_text(project.seed_path)
    config = load_config(project)
    decomposition = decompose_seed(seed_text, config)
    write_json(state_path, decomposition)
    write_text(project.reports_dir / "decomposition.md", render_decomposition(decomposition))
    return decomposition


def ensure_matrix(project: Any, *, refresh: bool) -> dict[str, Any]:
    state_path = project.state_dir / "novelty_matrix.json"
    if state_path.exists() and not refresh:
        return read_json(state_path, {})
    decomposition = ensure_decomposition(project, refresh=False)
    papers = load_all_papers(project)
    config = load_config(project)
    matrix = build_novelty_matrix(decomposition, papers, config)
    write_json(state_path, matrix)
    write_text(project.reports_dir / "novelty_matrix.md", render_matrix(matrix))
    return matrix


def ensure_refined_ideas(project: Any, *, refresh: bool) -> list[dict[str, Any]]:
    state_path = project.state_dir / "refined_ideas.json"
    if state_path.exists() and not refresh:
        return read_json(state_path, [])
    decomposition = ensure_decomposition(project, refresh=False)
    matrix = ensure_matrix(project, refresh=False)
    ideas = refine_ideas(decomposition, matrix)
    write_json(state_path, ideas)
    write_text(project.reports_dir / "refined_ideas.md", render_refined_ideas(ideas))
    return ideas


def ensure_experiment_plan(project: Any, *, refresh: bool) -> dict[str, Any]:
    state_path = project.state_dir / "experiment_plan.json"
    if state_path.exists() and not refresh:
        return read_json(state_path, {})
    decomposition = ensure_decomposition(project, refresh=False)
    matrix = ensure_matrix(project, refresh=False)
    plan = build_experiment_plan(decomposition, matrix)
    write_json(state_path, plan)
    write_text(project.reports_dir / "experiment_plan.md", render_experiment_plan(plan))
    return plan


def ensure_final_report(project: Any) -> Path:
    decomposition = read_text(project.reports_dir / "decomposition.md")
    matrix = read_text(project.reports_dir / "novelty_matrix.md")
    refined = read_text(project.reports_dir / "refined_ideas.md")
    experiment = read_text(project.reports_dir / "experiment_plan.md")
    if not decomposition:
        ensure_decomposition(project, refresh=False)
        decomposition = read_text(project.reports_dir / "decomposition.md")
    if not matrix:
        ensure_matrix(project, refresh=False)
        matrix = read_text(project.reports_dir / "novelty_matrix.md")
    if not refined:
        ensure_refined_ideas(project, refresh=False)
        refined = read_text(project.reports_dir / "refined_ideas.md")
    if not experiment:
        ensure_experiment_plan(project, refresh=False)
        experiment = read_text(project.reports_dir / "experiment_plan.md")
    final_path = project.reports_dir / "final_report_cn.md"
    write_text(final_path, render_final_report(decomposition, matrix, refined, experiment))
    return final_path


def load_all_papers(project: Any) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for path in sorted(project.papers_dir.glob("*.json")):
        data = read_json(path, [])
        if isinstance(data, list):
            papers.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict) and isinstance(data.get("papers"), list):
            papers.extend(item for item in data["papers"] if isinstance(item, dict))
    return dedupe_by_title(papers)


def dedupe_by_title(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for paper in papers:
        title = str(paper.get("title", "")).strip().lower()
        if not title or title in seen:
            continue
        seen.add(title)
        deduped.append(paper)
    return deduped


def parse_sources(value: str | None, config: dict[str, Any]) -> list[str]:
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    configured = config.get("search_sources", ["arxiv", "openalex", "semantic_scholar"])
    if isinstance(configured, list):
        return [str(item) for item in configured]
    return [item.strip() for item in str(configured).split(",") if item.strip()]


def parse_sources_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_query_file(text: str) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- id:"):
            if current and current.get("query"):
                queries.append(current)
            current = {"id": stripped.split(":", 1)[1].strip()}
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = value.strip()
    if current and current.get("query"):
        queries.append(current)

    normalized: list[dict[str, str]] = []
    for index, query in enumerate(queries, start=1):
        normalized.append(
            {
                "id": query.get("id") or f"Q-manual-{index}",
                "claim_id": query.get("claim_id") or "manual",
                "intent": query.get("intent") or "manual",
                "query": query["query"],
            }
        )
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())

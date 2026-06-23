from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .project import IdeaProject, detail_report_path, read_json, write_json, write_text
from .render import md_table, timestamp


STORE_VERSION = 2
PASSAGE_CHARS = 1200
PASSAGE_OVERLAP = 150
MAX_PASSAGES_PER_PAPER = 80

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}

STAGE_PROFILES: dict[str, dict[str, Any]] = {
    "novelty_matrix_builder": {
        "focus": "claim-level prior-work overlap, concrete differences, missing evidence, PDF passage evidence",
        "types": {"overlap_risk", "abstract", "failure_signal", "benchmark_signal", "reviewer_attack"},
        "keywords": [
            "prior",
            "related work",
            "overlap",
            "novelty",
            "difference",
            "limitation",
            "failure",
            "benchmark",
            "evaluation",
            "baseline",
            "method",
            "experiment",
        ],
    },
    "bottleneck_extractor": {
        "focus": "technical bottlenecks, failure modes, hidden assumptions, benchmark gaps",
        "types": {"failure_signal", "benchmark_signal", "overlap_risk", "reviewer_attack", "abstract"},
        "keywords": [
            "failure",
            "limitation",
            "bottleneck",
            "benchmark",
            "evaluation",
            "ablation",
            "negative",
            "cannot",
            "hard",
            "challenge",
        ],
    },
    "mechanism_transfer_mapper": {
        "focus": "adjacent mechanisms and transferable methods",
        "types": {"method_inspiration", "adjacent_transfer", "abstract"},
        "keywords": [
            "mechanism",
            "transfer",
            "causal",
            "representation",
            "intervention",
            "regularization",
            "factor",
            "latent",
            "control",
            "planner",
        ],
    },
    "idea_branch_generator": {
        "focus": "opportunity map, branch diversity, concise prior-work warnings",
        "types": {"method_inspiration", "adjacent_transfer", "overlap_risk", "benchmark_signal"},
        "keywords": [
            "opportunity",
            "method",
            "diagnostic",
            "benchmark",
            "novelty",
            "controllable",
            "world model",
            "action",
            "experiment",
        ],
    },
    "branch_screener": {
        "focus": "closest prior work, overlap risk, fatal objections, evidence gaps",
        "types": {"overlap_risk", "reviewer_attack", "failure_signal", "abstract"},
        "keywords": [
            "prior",
            "overlap",
            "novelty",
            "similar",
            "already",
            "reviewer",
            "attack",
            "risk",
            "baseline",
        ],
    },
    "idea_strengthener": {
        "focus": "salvage paths, sharper mechanisms, evidence needs, minimum experiments",
        "types": {"method_inspiration", "benchmark_signal", "overlap_risk", "failure_signal"},
        "keywords": [
            "salvage",
            "strengthen",
            "mechanism",
            "minimum",
            "experiment",
            "ablation",
            "metric",
            "baseline",
            "risk",
        ],
    },
    "decision_chair": {
        "focus": "must-read papers, global risks, final prior-work attacks, decision evidence",
        "types": {"overlap_risk", "reviewer_attack", "failure_signal", "benchmark_signal", "abstract"},
        "keywords": [
            "must",
            "global",
            "risk",
            "prior",
            "evidence",
            "decision",
            "novelty",
            "failure",
            "benchmark",
        ],
    },
}


def build_or_load_literature_store(
    project: IdeaProject,
    *,
    papers: list[dict[str, Any]],
    brief: dict[str, Any],
    claims: dict[str, Any],
    novelty_matrix: dict[str, Any],
    reviewer_report: dict[str, Any],
    evidence_qa: dict[str, Any],
    refresh: bool = False,
    progress=None,
) -> dict[str, Any]:
    path = project.state_dir / "literature_store.json"
    input_signature = literature_store_signature(
        project=project,
        papers=papers,
        novelty_matrix=novelty_matrix,
        reviewer_report=reviewer_report,
        evidence_qa=evidence_qa,
    )
    if path.exists() and not refresh:
        data = read_json(path, {})
        if (
            isinstance(data, dict)
            and data.get("version") == STORE_VERSION
            and data.get("input_signature") == input_signature
        ):
            log_progress(progress, f"literature store: cache hit ({data.get('paper_count', 0)} papers, {data.get('passage_count', 0)} PDF passages)")
            return data

    log_progress(progress, f"literature store: rebuilding ({len(papers)} papers)")
    store = build_literature_store(
        project,
        papers=papers,
        brief=brief,
        claims=claims,
        novelty_matrix=novelty_matrix,
        reviewer_report=reviewer_report,
        evidence_qa=evidence_qa,
    )
    store["input_signature"] = input_signature
    write_json(path, store)
    write_text(detail_report_path(project, "literature_store.md"), render_literature_store(store))
    log_progress(progress, f"literature store: ready ({store.get('passage_count', 0)} PDF passages, {store.get('evidence_count', 0)} evidence items)")
    return store


def literature_store_signature(
    *,
    project: IdeaProject,
    papers: list[dict[str, Any]],
    novelty_matrix: dict[str, Any],
    reviewer_report: dict[str, Any],
    evidence_qa: dict[str, Any],
) -> dict[str, Any]:
    return {
        "signature_version": 2,
        "paper_count": len(papers),
        "papers": [paper_signature(project, paper, index) for index, paper in enumerate(papers, start=1)],
        "novelty_matrix_hash": stable_json_hash(novelty_matrix),
        "reviewer_report_hash": stable_json_hash(reviewer_report),
        "evidence_qa_hash": stable_json_hash(evidence_qa),
    }


def paper_signature(project: IdeaProject, paper: dict[str, Any], index: int) -> dict[str, Any]:
    local_pdf = normalize_pdf_path(project, paper)
    return {
        "paper_id": stable_paper_id(paper, index),
        "title": paper.get("title", ""),
        "year": paper.get("year", "") or str(paper.get("published_date", ""))[:4],
        "source": paper.get("source", ""),
        "venue": paper.get("venue", ""),
        "url": paper.get("url", ""),
        "doi": paper.get("doi", ""),
        "arxiv_id": paper.get("arxiv_id", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "local_pdf": str(local_pdf) if local_pdf else "",
        "abstract_hash": stable_text_hash(paper.get("abstract", "")),
        "pdf_file": file_signature(local_pdf),
    }


def file_signature(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"exists": False}
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def stable_text_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def log_progress(progress, message: str) -> None:
    if progress:
        progress(message)


def build_literature_store(
    project: IdeaProject,
    *,
    papers: list[dict[str, Any]],
    brief: dict[str, Any],
    claims: dict[str, Any],
    novelty_matrix: dict[str, Any],
    reviewer_report: dict[str, Any],
    evidence_qa: dict[str, Any],
) -> dict[str, Any]:
    paper_entries: list[dict[str, Any]] = []
    passages: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    errors: list[str] = []

    for index, paper in enumerate(papers, start=1):
        entry = build_paper_entry(project, paper, index)
        paper_entries.append(entry)
        if entry.get("abstract"):
            evidence_items.append(
                {
                    "id": f"E-abstract-{entry['paper_id']}",
                    "type": "abstract",
                    "paper_id": entry["paper_id"],
                    "title": entry["title"],
                    "text": truncate_text(entry["abstract"], 1200),
                    "source": "paper_metadata",
                }
            )

        local_pdf = entry.get("local_pdf", "")
        if local_pdf:
            extracted, pdf_errors = extract_pdf_passages(Path(local_pdf), entry)
            passages.extend(extracted)
            errors.extend(pdf_errors)

    evidence_items.extend(matrix_evidence_items(novelty_matrix, paper_entries))
    evidence_items.extend(reviewer_evidence_items(reviewer_report))
    evidence_items.extend(evidence_qa_items(evidence_qa, paper_entries))

    return {
        "version": STORE_VERSION,
        "generated_at": timestamp(),
        "topic": brief.get("topic", ""),
        "paper_count": len(paper_entries),
        "passage_count": len(passages),
        "evidence_count": len(evidence_items),
        "papers": paper_entries,
        "passages": passages,
        "evidence_items": evidence_items,
        "errors": errors,
    }


def build_paper_entry(project: IdeaProject, paper: dict[str, Any], index: int) -> dict[str, Any]:
    paper_id = stable_paper_id(paper, index)
    local_pdf = normalize_pdf_path(project, paper)
    return {
        "paper_id": paper_id,
        "title": str(paper.get("title") or f"paper {index}").strip(),
        "year": paper.get("year", "") or str(paper.get("published_date", ""))[:4],
        "source": paper.get("source", ""),
        "venue": paper.get("venue", ""),
        "url": paper.get("url", ""),
        "doi": paper.get("doi", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "local_pdf": str(local_pdf) if local_pdf else "",
        "abstract": truncate_text(paper.get("abstract", ""), 2400),
        "has_local_pdf": bool(local_pdf and local_pdf.exists()),
    }


def stable_paper_id(paper: dict[str, Any], index: int) -> str:
    for field in ("paper_id", "doi", "arxiv_id", "url", "pdf_url", "local_pdf", "title"):
        value = str(paper.get(field) or "").strip()
        if value:
            return "P-" + safe_id(value)
    return f"P-{index:04d}"


def safe_id(value: str) -> str:
    lowered = value.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return (cleaned or "paper")[:80]


def normalize_pdf_path(project: IdeaProject, paper: dict[str, Any]) -> Path | None:
    raw = str(paper.get("local_pdf") or paper.get("pdf_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def extract_pdf_passages(path: Path, paper: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"missing local_pdf for {paper.get('title', '')}: {path}"]

    try:
        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001 - optional import should degrade cleanly.
        return [], [f"pypdf unavailable; skipped PDF text for {paper.get('title', '')}: {exc}"]

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - keep per-PDF failure non-fatal.
        return [], [f"failed to open PDF for {paper.get('title', '')}: {exc}"]

    passages: list[dict[str, Any]] = []
    errors: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        if len(passages) >= MAX_PASSAGES_PER_PAPER:
            break
        try:
            page_text = normalize_space(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - keep partial extraction.
            errors.append(f"failed to extract page {page_index} for {paper.get('title', '')}: {exc}")
            continue
        for chunk_index, chunk in enumerate(chunk_text(page_text), start=1):
            if len(passages) >= MAX_PASSAGES_PER_PAPER:
                break
            passage_id = f"PASS-{paper['paper_id']}-{page_index}-{chunk_index}"
            passages.append(
                {
                    "id": passage_id,
                    "paper_id": paper["paper_id"],
                    "title": paper["title"],
                    "page": page_index,
                    "local_pdf": str(path),
                    "url": paper.get("url", ""),
                    "pdf_url": paper.get("pdf_url", ""),
                    "text": chunk,
                    "tags": infer_text_tags(chunk),
                }
            )
    return passages, errors


def chunk_text(text: str) -> list[str]:
    if not text:
        return []
    if len(text) <= PASSAGE_CHARS:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + PASSAGE_CHARS)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - PASSAGE_OVERLAP, start + 1)
    return chunks


def infer_text_tags(text: str) -> list[str]:
    lowered = text.lower()
    tags: list[str] = []
    if any(token in lowered for token in ("limitation", "failure", "challenge", "cannot", "hard to")):
        tags.append("failure_signal")
    if any(token in lowered for token in ("benchmark", "evaluation", "metric", "ablation", "baseline")):
        tags.append("benchmark_signal")
    if any(token in lowered for token in ("method", "architecture", "framework", "algorithm", "model")):
        tags.append("method_inspiration")
    if any(token in lowered for token in ("similar", "related work", "prior work", "overlap")):
        tags.append("overlap_risk")
    if any(token in lowered for token in ("transfer", "causal", "intervention", "representation", "factor")):
        tags.append("adjacent_transfer")
    return tags


def matrix_evidence_items(matrix: dict[str, Any], papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    title_to_id = {normalize_title(paper.get("title", "")): paper["paper_id"] for paper in papers}
    items: list[dict[str, Any]] = []
    for row_index, row in enumerate(matrix.get("rows", []), start=1):
        for paper_index, paper in enumerate(row.get("closest_papers", []), start=1):
            title = str(paper.get("title", "")).strip()
            text = " ".join(
                str(value)
                for value in (
                    paper.get("overlap", ""),
                    paper.get("difference", ""),
                    row.get("positioning", ""),
                )
                if value
            )
            if not title and not text:
                continue
            items.append(
                {
                    "id": f"E-matrix-{row_index}-{paper_index}",
                    "type": "overlap_risk",
                    "paper_id": title_to_id.get(normalize_title(title), ""),
                    "title": title,
                    "claim_id": row.get("claim_id", ""),
                    "risk": row.get("risk", ""),
                    "text": truncate_text(text, 1200),
                    "source": "novelty_matrix",
                }
            )
        for missing_index, missing in enumerate(row.get("missing_evidence", []), start=1):
            items.append(
                {
                    "id": f"E-missing-{row_index}-{missing_index}",
                    "type": "failure_signal",
                    "paper_id": "",
                    "title": "",
                    "claim_id": row.get("claim_id", ""),
                    "risk": row.get("risk", ""),
                    "text": truncate_text(missing, 700),
                    "source": "novelty_matrix_missing_evidence",
                }
            )
    return items


def reviewer_evidence_items(review: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    fields = [
        ("strongest_objections", "failure_signal"),
        ("reviewer_likely_prior_work_attack", "reviewer_attack"),
        ("experiment_concerns", "benchmark_signal"),
        ("minimum_fixes", "method_inspiration"),
    ]
    for field, item_type in fields:
        for index, text in enumerate(review.get(field, []), start=1):
            items.append(
                {
                    "id": f"E-review-{field}-{index}",
                    "type": item_type,
                    "paper_id": "",
                    "title": "",
                    "text": truncate_text(text, 900),
                    "source": f"reviewer_report.{field}",
                }
            )
    advice = str(review.get("positioning_advice") or "").strip()
    if advice:
        items.append(
            {
                "id": "E-review-positioning",
                "type": "overlap_risk",
                "paper_id": "",
                "title": "",
                "text": truncate_text(advice, 1000),
                "source": "reviewer_report.positioning_advice",
            }
        )
    return items


def evidence_qa_items(evidence_qa: dict[str, Any], papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    selected = evidence_qa.get("selected_papers", [])
    if isinstance(selected, list):
        known = {normalize_title(paper.get("title", "")): paper["paper_id"] for paper in papers}
        for index, paper in enumerate(selected, start=1):
            if not isinstance(paper, dict):
                continue
            title = str(paper.get("title") or "").strip()
            items.append(
                {
                    "id": f"E-evidenceqa-selected-{index}",
                    "type": "abstract",
                    "paper_id": known.get(normalize_title(title), ""),
                    "title": title,
                    "text": f"Evidence QA selected this paper for local PDF analysis: {title}",
                    "source": "evidence_qa.selected_papers",
                }
            )
    return items


def retrieve_stage_context(
    *,
    store: dict[str, Any],
    stage: str,
    base_context: dict[str, Any],
    extra_context: dict[str, Any] | None = None,
    include_extra_context: bool = True,
    evidence_limit: int = 10,
    passage_limit: int = 6,
) -> dict[str, Any]:
    profile = STAGE_PROFILES.get(stage, STAGE_PROFILES["decision_chair"])
    extra_context = extra_context or {}
    query_terms = collect_query_terms(base_context, extra_context, profile.get("keywords", []))
    evidence = select_evidence_items(store, profile, query_terms, limit=evidence_limit)
    passages = select_passages(store, profile, query_terms, limit=passage_limit)
    selected_paper_ids = {
        item.get("paper_id", "")
        for item in evidence + passages
        if item.get("paper_id")
    }
    papers = [
        compact_paper_for_context(paper)
        for paper in store.get("papers", [])
        if paper.get("paper_id") in selected_paper_ids
    ]

    context = {
        "stage": stage,
        "stage_focus": profile["focus"],
        "brief": compact_brief(base_context.get("brief", {})),
        "claims": compact_claims(base_context.get("claims", {})),
        "novelty_summary": compact_novelty(base_context.get("novelty_matrix", {}), stage),
        "reviewer_summary": compact_review(base_context.get("reviewer_report", {}), stage),
        "evidence_summary": {
            "store_papers": store.get("paper_count", 0),
            "store_passages": store.get("passage_count", 0),
            "store_evidence_items": store.get("evidence_count", 0),
            "selected_papers": len(papers),
            "selected_evidence_items": len(evidence),
            "selected_passages": len(passages),
            "omitted_evidence_items": max(int(store.get("evidence_count", 0)) - len(evidence), 0),
            "omitted_passages": max(int(store.get("passage_count", 0)) - len(passages), 0),
        },
        "selected_papers": papers,
        "evidence_items": evidence,
        "paper_passages": passages,
    }
    if include_extra_context:
        context.update(extra_context)
    return context


def retrieve_novelty_claim_context(
    *,
    store: dict[str, Any],
    base_context: dict[str, Any],
    claim: dict[str, Any],
    evidence_qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = retrieve_stage_context(
        store=store,
        stage="novelty_matrix_builder",
        base_context=base_context,
        extra_context={
            "target_claim": {
                "id": claim.get("id", ""),
                "claim": claim.get("claim", ""),
                "mechanism": claim.get("mechanism", ""),
                "task_context": claim.get("task_context", ""),
                "risk_if_false": claim.get("risk_if_false", ""),
                "equivalent_terms": claim.get("equivalent_terms", []),
            },
            "evidence_qa_status": {
                "status": (evidence_qa or {}).get("status", ""),
                "reason": (evidence_qa or {}).get("reason", ""),
            },
        },
        evidence_limit=12,
        passage_limit=8,
    )
    context.pop("brief", None)
    context.pop("claims", None)
    context.pop("novelty_summary", None)
    context.pop("reviewer_summary", None)
    context["selected_papers"] = context.get("selected_papers", [])[:10]
    context["evidence_items"] = [
        {
            **item,
            "text": truncate_text(item.get("text", ""), 550),
        }
        for item in context.get("evidence_items", [])[:12]
        if isinstance(item, dict)
    ]
    context["paper_passages"] = [
        {
            **item,
            "text": truncate_text(item.get("text", ""), 700),
        }
        for item in context.get("paper_passages", [])[:8]
        if isinstance(item, dict)
    ]
    context["instruction"] = (
        "Use this as compressed claim-level RAG evidence. PDF passages are excerpts, not full papers; "
        "prefer them over metadata when judging overlap, but keep missing evidence explicit."
    )
    return context


def select_evidence_items(
    store: dict[str, Any],
    profile: dict[str, Any],
    query_terms: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    typed = set(profile.get("types", set()))
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in store.get("evidence_items", []):
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key, "")) for key in ("title", "text", "claim_id", "risk", "source"))
        score = score_text(text, query_terms)
        if item.get("type") in typed:
            score += 8
        if item.get("risk") == "high":
            score += 3
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [compact_evidence_item(item) for _, item in scored[:limit]]


def select_passages(
    store: dict[str, Any],
    profile: dict[str, Any],
    query_terms: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    typed = set(profile.get("types", set()))
    scored: list[tuple[int, dict[str, Any]]] = []
    for passage in store.get("passages", []):
        if not isinstance(passage, dict):
            continue
        text = " ".join(str(passage.get(key, "")) for key in ("title", "text"))
        score = score_text(text, query_terms)
        tags = set(passage.get("tags", []))
        score += len(tags & typed) * 6
        if score > 0:
            scored.append((score, passage))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [compact_passage(passage) for _, passage in scored[:limit]]


def score_text(text: str, query_terms: set[str]) -> int:
    lowered = text.lower()
    score = 0
    for term in query_terms:
        if not term:
            continue
        if " " in term:
            if term in lowered:
                score += 5
            continue
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            score += 1
    return score


def collect_query_terms(base_context: dict[str, Any], extra_context: dict[str, Any], keywords: list[str]) -> set[str]:
    text = " ".join(
        [
            str(base_context.get("brief", {})),
            str(base_context.get("claims", {})),
            str(extra_context),
        ]
    )
    ordered_terms: list[str] = []
    seen: set[str] = set()
    for term in keywords:
        cleaned = term.lower().strip()
        if cleaned and cleaned not in seen:
            ordered_terms.append(cleaned)
            seen.add(cleaned)
    for token in tokenize(text):
        if token in STOPWORDS or len(token) < 3 or token in seen:
            continue
        ordered_terms.append(token)
        seen.add(token)
        if len(ordered_terms) >= 180:
            break
    return set(ordered_terms)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]+", text.lower())


def compact_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "type": item.get("type", ""),
        "paper_id": item.get("paper_id", ""),
        "title": item.get("title", ""),
        "claim_id": item.get("claim_id", ""),
        "risk": item.get("risk", ""),
        "source": item.get("source", ""),
        "text": truncate_text(item.get("text", ""), 650),
    }


def compact_passage(passage: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": passage.get("id", ""),
        "paper_id": passage.get("paper_id", ""),
        "title": passage.get("title", ""),
        "page": passage.get("page", ""),
        "local_pdf": passage.get("local_pdf", ""),
        "url": passage.get("url", ""),
        "pdf_url": passage.get("pdf_url", ""),
        "tags": passage.get("tags", []),
        "text": truncate_text(passage.get("text", ""), 750),
    }


def compact_paper_for_context(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year", ""),
        "source": paper.get("source", ""),
        "url": paper.get("url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "local_pdf": paper.get("local_pdf", ""),
        "has_local_pdf": paper.get("has_local_pdf", False),
    }


def compact_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": brief.get("topic", ""),
        "problem_statement": truncate_text(brief.get("problem_statement", ""), 900),
        "domain": brief.get("domain", [])[:8],
        "known_context": brief.get("known_context", [])[:8],
        "uncertainties": brief.get("uncertainties", [])[:8],
    }


def compact_claims(claims: dict[str, Any]) -> dict[str, Any]:
    return {
        "claims": [
            {
                "id": claim.get("id", ""),
                "type": claim.get("type", ""),
                "claim": truncate_text(claim.get("claim", ""), 380),
                "mechanism": truncate_text(claim.get("mechanism", ""), 260),
                "task_context": truncate_text(claim.get("task_context", ""), 220),
                "risk_if_false": truncate_text(claim.get("risk_if_false", ""), 220),
                "equivalent_terms": claim.get("equivalent_terms", [])[:8],
            }
            for claim in claims.get("claims", [])[:8]
            if isinstance(claim, dict)
        ],
        "risk_questions": [truncate_text(question, 220) for question in claims.get("risk_questions", [])[:8]],
    }


def compact_novelty(matrix: dict[str, Any], stage: str) -> dict[str, Any]:
    rows = []
    row_limit = 6 if stage in {"branch_screener", "decision_chair"} else 4
    for row in matrix.get("rows", [])[:row_limit]:
        rows.append(
            {
                "claim_id": row.get("claim_id", ""),
                "claim": truncate_text(row.get("claim", ""), 340),
                "risk": row.get("risk", ""),
                "closest_papers": [
                    {
                        "title": paper.get("title", ""),
                        "year": paper.get("year", ""),
                        "overlap": truncate_text(paper.get("overlap", ""), 240),
                        "difference": truncate_text(paper.get("difference", ""), 240),
                    }
                    for paper in row.get("closest_papers", [])[:2]
                ],
                "positioning": truncate_text(row.get("positioning", ""), 320),
            }
        )
    return {
        "overall_recommendation": matrix.get("overall_recommendation", ""),
        "rows": rows,
    }


def compact_review(review: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage in {"mechanism_transfer_mapper", "idea_branch_generator"}:
        return {
            "summary": truncate_text(review.get("summary", ""), 600),
            "minimum_fixes": review.get("minimum_fixes", [])[:4],
            "positioning_advice": truncate_text(review.get("positioning_advice", ""), 600),
        }
    return {
        "summary": truncate_text(review.get("summary", ""), 700),
        "score": review.get("score", ""),
        "recommendation": review.get("recommendation", ""),
        "strongest_objections": review.get("strongest_objections", [])[:6],
        "reviewer_likely_prior_work_attack": review.get("reviewer_likely_prior_work_attack", [])[:6],
        "experiment_concerns": review.get("experiment_concerns", [])[:6],
        "positioning_advice": truncate_text(review.get("positioning_advice", ""), 700),
    }


def render_literature_store(store: dict[str, Any]) -> str:
    lines = [
        "# Literature Store",
        "",
        f"- generated_at: {store.get('generated_at', '')}",
        f"- papers: {store.get('paper_count', 0)}",
        f"- PDF passages: {store.get('passage_count', 0)}",
        f"- evidence items: {store.get('evidence_count', 0)}",
        "",
        "## Papers",
        "",
    ]
    rows = [
        [
            paper.get("paper_id", ""),
            paper.get("title", ""),
            paper.get("year", ""),
            "yes" if paper.get("has_local_pdf") else "no",
        ]
        for paper in store.get("papers", [])[:60]
    ]
    lines.append(md_table(["id", "title", "year", "local PDF"], rows) if rows else "No papers indexed.")
    errors = store.get("errors", [])
    if errors:
        lines.extend(["", "## Extraction Notes", ""])
        for error in errors[:40]:
            lines.append(f"- {error}")
    return "\n".join(lines).strip() + "\n"


def normalize_title(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_space(value: str) -> str:
    return " ".join(str(value or "").split())


def truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

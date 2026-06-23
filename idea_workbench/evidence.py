from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ModelConfigError, get_model_tier
from .project import detail_report_path
from .render import md_table


@dataclass
class EvidenceStatus:
    available: bool
    backend: str
    reason: str
    pqa_path: str = ""
    paperqa_import: bool = False


def detect_evidence_backend() -> EvidenceStatus:
    pqa_path = shutil.which("pqa") or ""
    paperqa_import = importlib.util.find_spec("paperqa") is not None
    if pqa_path or paperqa_import:
        return EvidenceStatus(
            available=True,
            backend="paperqa2",
            reason="PaperQA2 detected",
            pqa_path=pqa_path,
            paperqa_import=paperqa_import,
        )
    return EvidenceStatus(
        available=False,
        backend="paperqa2",
        reason="PaperQA2 is not installed; install `paper-qa` to enable PDF evidence QA.",
    )


def run_evidence_qa(
    project: Any,
    config: dict[str, Any],
    claims_doc: dict[str, Any],
    papers: list[dict[str, Any]],
    *,
    mock: bool | None = None,
) -> dict[str, Any]:
    settings = config.get("evidence_qa", {})
    enabled = bool(settings.get("enabled", True))
    if not enabled:
        result = {
            "status": "disabled",
            "backend": settings.get("backend", "paperqa2"),
            "reason": "evidence_qa.enabled is false",
            "items": [],
        }
        write_evidence_outputs(project, result)
        return result

    use_mock = bool(settings.get("mock", False)) if mock is None else mock
    claims = list(claims_doc.get("claims", []))[: int(settings.get("max_claims", 8))]
    selected_papers = select_papers_with_pdf(papers, max_papers=int(settings.get("max_papers", 8)))
    selected_papers = normalize_selected_pdf_paths(project, selected_papers)

    if use_mock:
        result = mock_evidence(claims, selected_papers)
        write_evidence_outputs(project, result)
        return result

    status = detect_evidence_backend()
    if not status.available:
        result = {
            "status": "unavailable",
            "backend": status.backend,
            "reason": status.reason,
            "items": [],
            "selected_papers": selected_papers,
        }
        write_evidence_outputs(project, result)
        return result

    if settings.get("require_pdf", True) and not selected_papers:
        result = {
            "status": "no_pdf",
            "backend": status.backend,
            "reason": "No papers with local PDF path or pdf_url were available for PaperQA2.",
            "items": [],
            "selected_papers": selected_papers,
        }
        write_evidence_outputs(project, result)
        return result

    local_pdf_paths = [
        Path(paper["local_pdf"])
        for paper in selected_papers
        if paper.get("local_pdf") and Path(str(paper["local_pdf"])).exists()
    ]
    if not local_pdf_paths:
        result = {
            "status": "needs_pdf_download",
            "backend": status.backend,
            "reason": "PaperQA2 detected, but no readable local PDFs are present. Run `pdfs` or add valid local_pdf fields.",
            "items": [],
            "selected_papers": selected_papers,
        }
        write_evidence_outputs(project, result)
        return result

    items: list[dict[str, Any]] = []
    for claim in claims:
        question = build_claim_question(claim)
        answer = ask_paperqa_cli(project, config, question, local_pdf_paths, status)
        items.append(
            {
                "claim_id": claim.get("id", ""),
                "claim": claim.get("claim", ""),
                "question": question,
                "answer": answer.get("answer", ""),
                "backend": "paperqa2",
                "evidence_strength": "paperqa_answer",
                "sources": answer.get("sources", []),
                "status": answer.get("status", "ok"),
                "error": answer.get("error", ""),
            }
        )

    result = {
        "status": "ok",
        "backend": status.backend,
        "reason": "Evidence QA completed for local PDFs.",
        "items": items,
        "selected_papers": selected_papers,
    }
    write_evidence_outputs(project, result)
    return result


def select_papers_with_pdf(papers: list[dict[str, Any]], *, max_papers: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for paper in papers:
        local_pdf = str(paper.get("local_pdf") or paper.get("pdf_path") or "").strip()
        pdf_url = str(paper.get("pdf_url") or "").strip()
        if not local_pdf and not pdf_url:
            continue
        selected.append(
            {
                "title": paper.get("title", ""),
                "year": paper.get("year", "") or str(paper.get("published_date", ""))[:4],
                "url": paper.get("url", ""),
                "pdf_url": pdf_url,
                "local_pdf": local_pdf,
                "source": paper.get("source", ""),
            }
        )
        if len(selected) >= max_papers:
            break
    return selected


def normalize_selected_pdf_paths(project: Any, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for paper in papers:
        item = dict(paper)
        local_pdf = str(item.get("local_pdf") or "").strip()
        if local_pdf:
            item["local_pdf"] = str(resolve_local_pdf_path(project, local_pdf))
        normalized.append(item)
    return normalized


def resolve_local_pdf_path(project: Any, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    project_relative = (project.root / path).resolve()
    if project_relative.exists():
        return project_relative
    cwd_relative = (Path.cwd() / path).resolve()
    if cwd_relative.exists():
        return cwd_relative
    return project_relative


def build_claim_question(claim: dict[str, Any]) -> str:
    equivalent = ", ".join(claim.get("equivalent_terms", []) or [])
    parts = [
        "Does the provided paper corpus already implement, test, or strongly imply this research claim?",
        f"Claim: {claim.get('claim', '')}",
    ]
    if equivalent:
        parts.append(f"Also check renamed or equivalent concepts: {equivalent}")
    parts.append("Answer with overlap, key difference, and cite evidence if available.")
    return "\n".join(parts)


def ask_paperqa_cli(project: Any, config: dict[str, Any], question: str, pdfs: list[Path], status: EvidenceStatus) -> dict[str, Any]:
    if not status.pqa_path:
        return {
            "status": "unavailable",
            "answer": "",
            "sources": [],
            "error": "pqa CLI not found; Python paperqa import is not yet used by this adapter.",
        }
    paper_dir = project.evidence_dir / "paperqa_pdfs"
    paper_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        if pdf.exists():
            target = paper_dir / pdf.name
            if not target.exists():
                target.write_bytes(pdf.read_bytes())
    try:
        result = subprocess.run(
            [status.pqa_path, "ask", question],
            cwd=paper_dir,
            env=build_paperqa_env(config),
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - optional adapter.
        return {"status": "error", "answer": "", "sources": [], "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "answer": result.stdout.strip(), "sources": [], "error": result.stderr.strip()}
    return {"status": "ok", "answer": result.stdout.strip(), "sources": []}


def build_paperqa_env(config: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    for tier_name in ("standard", "strong", "frontier", "cheap"):
        try:
            tier = get_model_tier(config, tier_name)
        except ModelConfigError:
            continue
        if tier.api_key and not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = tier.api_key
        if tier.base_url and not env.get("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = tier.base_url
        if tier.base_url and not env.get("OPENAI_API_BASE"):
            env["OPENAI_API_BASE"] = tier.base_url
        if env.get("OPENAI_API_KEY") and env.get("OPENAI_BASE_URL"):
            break
    return env


def mock_evidence(claims: list[dict[str, Any]], papers: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for claim in claims:
        items.append(
            {
                "claim_id": claim.get("id", ""),
                "claim": claim.get("claim", ""),
                "question": build_claim_question(claim),
                "answer": "MOCK: The corpus partially overlaps with the claim, but the controllability framing remains unresolved.",
                "backend": "mock",
                "evidence_strength": "weak",
                "sources": [paper.get("title", "") for paper in papers[:2]],
                "status": "ok",
                "error": "",
            }
        )
    return {
        "status": "ok",
        "backend": "mock",
        "reason": "Mock evidence QA used for tests or dry validation.",
        "items": items,
        "selected_papers": papers,
    }


def write_evidence_outputs(project: Any, result: dict[str, Any]) -> None:
    project.evidence_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = project.evidence_dir / "claim_evidence.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as file_obj:
        for item in result.get("items", []):
            file_obj.write(json.dumps(item, ensure_ascii=False) + "\n")
    (project.evidence_dir / "evidence_status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = detail_report_path(project, "evidence_qa.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_evidence_report(result), encoding="utf-8")


def render_evidence_report(result: dict[str, Any]) -> str:
    lines = [
        "# Evidence QA Report",
        "",
        f"- Status: {result.get('status', '')}",
        f"- Backend: {result.get('backend', '')}",
        f"- Reason: {result.get('reason', '')}",
        "",
    ]
    selected = result.get("selected_papers", [])
    if selected:
        lines.extend(["## Selected Papers", ""])
        rows = [[paper.get("title", ""), paper.get("year", ""), paper.get("source", ""), paper.get("local_pdf") or paper.get("pdf_url", "")] for paper in selected]
        lines.append(md_table(["title", "year", "source", "pdf"], rows))
        lines.append("")
    items = result.get("items", [])
    if not items:
        lines.extend(
            [
                "## Evidence",
                "",
                "No claim-level evidence was generated. This usually means PaperQA2 is unavailable or no local PDFs are present.",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    lines.extend(["## Claim Evidence", ""])
    rows = [
        [
            item.get("claim_id", ""),
            item.get("status", ""),
            item.get("evidence_strength", ""),
            item.get("answer", ""),
            "; ".join(str(source) for source in item.get("sources", [])),
        ]
        for item in items
    ]
    lines.append(md_table(["claim", "status", "strength", "answer", "sources"], rows))
    return "\n".join(lines).strip() + "\n"

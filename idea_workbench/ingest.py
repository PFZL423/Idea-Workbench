from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pdfs import extract_arxiv_id, safe_filename
from .project import detail_report_path
from .render import md_table, timestamp


ARXIV_URL_RE = re.compile(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/[^\s)>\]]+", re.IGNORECASE)
DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s<>\"]+)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"]+")


@dataclass
class IngestResult:
    papers: list[dict[str, Any]]
    index_path: Path
    report_path: Path
    inbox_dir: Path


def run_ingest(project: Any, *, inbox: str | Path | None = None, output: str = "imported_papers.json") -> IngestResult:
    inbox_dir = resolve_inbox_dir(project, inbox)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    papers: list[dict[str, Any]] = []
    notes: list[dict[str, str]] = []
    for path in sorted(inbox_dir.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        suffix = path.suffix.lower()
        before = len(papers)
        if suffix == ".pdf":
            papers.append(paper_from_pdf(project, path))
        elif suffix == ".bib":
            papers.extend(papers_from_bibtex(path))
        elif suffix == ".txt":
            papers.extend(papers_from_text_file(path))
        else:
            notes.append({"file": str(path), "status": "skipped", "detail": f"unsupported suffix {path.suffix}"})
            continue
        notes.append({"file": str(path), "status": "imported", "detail": f"{len(papers) - before} papers"})

    papers = dedupe_imported_papers(papers)
    index_path = project.papers_dir / output
    index_path.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = detail_report_path(project, "ingest.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_ingest_report(papers, notes, inbox_dir=inbox_dir, index_path=index_path), encoding="utf-8")
    return IngestResult(papers=papers, index_path=index_path, report_path=report_path, inbox_dir=inbox_dir)


def resolve_inbox_dir(project: Any, inbox: str | Path | None) -> Path:
    if inbox is None:
        return project.papers_dir / "inbox"
    path = Path(inbox).expanduser()
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def paper_from_pdf(project: Any, path: Path) -> dict[str, Any]:
    title = title_from_filename(path.stem)
    local_pdf = path.resolve()
    try:
        local_pdf = local_pdf.relative_to(project.root)
    except ValueError:
        pass
    return {
        "title": title,
        "source": "manual_pdf",
        "local_pdf": str(local_pdf),
        "imported_from": str(path),
    }


def papers_from_text_file(path: Path) -> list[dict[str, Any]]:
    name = path.name.lower()
    papers: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if "arxiv" in name:
            paper = paper_from_arxiv(value)
        elif "doi" in name:
            paper = paper_from_doi(value)
        elif "url" in name:
            paper = paper_from_url(value)
        else:
            paper = paper_from_reference_line(value)
        if paper:
            paper["imported_from"] = str(path)
            papers.append(paper)
    return papers


def paper_from_reference_line(value: str) -> dict[str, Any]:
    arxiv_id = extract_arxiv_id(value)
    if arxiv_id:
        return paper_from_arxiv(arxiv_id)
    doi_match = DOI_RE.search(value)
    if doi_match:
        return paper_from_doi(doi_match.group(1))
    url_match = URL_RE.search(value)
    if url_match:
        return paper_from_url(url_match.group(0))
    return {"title": value, "source": "manual_reference"}


def paper_from_arxiv(value: str) -> dict[str, Any]:
    arxiv_id = extract_arxiv_id(value) or value.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    arxiv_id = arxiv_id.strip()
    return {
        "title": f"arXiv {arxiv_id}",
        "source": "manual_arxiv",
        "arxiv_id": arxiv_id,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
    }


def paper_from_doi(value: str) -> dict[str, Any]:
    match = DOI_RE.search(value)
    doi = (match.group(1) if match else value).strip().rstrip(".,;")
    arxiv_id = extract_arxiv_id(doi)
    paper = {
        "title": f"DOI {doi}",
        "source": "manual_doi",
        "doi": doi,
        "url": f"https://doi.org/{doi}",
    }
    if arxiv_id:
        paper["arxiv_id"] = arxiv_id
        paper["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return paper


def paper_from_url(value: str) -> dict[str, Any]:
    url = value.strip()
    arxiv_id = extract_arxiv_id(url)
    if arxiv_id:
        return paper_from_arxiv(arxiv_id)
    return {
        "title": title_from_url(url),
        "source": "manual_url",
        "url": url,
    }


def papers_from_bibtex(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    entries = re.findall(r"@\w+\s*\{[^@]*", text, flags=re.DOTALL)
    papers: list[dict[str, Any]] = []
    for entry in entries:
        fields = parse_bibtex_fields(entry)
        if not fields:
            continue
        title = clean_bibtex_value(fields.get("title", ""))
        if not title:
            continue
        paper = {
            "title": title,
            "source": "manual_bibtex",
            "year": clean_bibtex_value(fields.get("year", "")),
            "venue": clean_bibtex_value(fields.get("journal", "") or fields.get("booktitle", "")),
            "authors": clean_bibtex_value(fields.get("author", "")),
            "url": clean_bibtex_value(fields.get("url", "")),
            "doi": clean_bibtex_value(fields.get("doi", "")),
            "abstract": clean_bibtex_value(fields.get("abstract", "")),
            "imported_from": str(path),
        }
        arxiv_id = extract_arxiv_id(" ".join(str(paper.get(key, "")) for key in ("url", "doi")))
        if arxiv_id:
            paper["arxiv_id"] = arxiv_id
            paper["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        papers.append({key: value for key, value in paper.items() if value not in ("", None, [], {})})
    return papers


def parse_bibtex_fields(entry: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pattern = re.compile(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)", re.DOTALL)
    for match in pattern.finditer(entry):
        fields[match.group(1).lower()] = match.group(2).strip().rstrip(",")
    return fields


def clean_bibtex_value(value: str) -> str:
    cleaned = value.strip().strip(",")
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (cleaned.startswith('"') and cleaned.endswith('"')):
        cleaned = cleaned[1:-1]
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.replace("{", "").replace("}", "").strip()


def title_from_filename(stem: str) -> str:
    text = re.sub(r"[_-]+", " ", stem)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Untitled PDF"


def title_from_url(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.split("?", 1)[0]
    if not tail:
        return url
    return title_from_filename(safe_filename(tail).removesuffix(".pdf"))


def dedupe_imported_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    by_key: dict[str, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for paper in papers:
        key = paper_identity(paper)
        if not key:
            continue
        if key in seen:
            existing = by_key[key]
            for field, value in paper.items():
                if value in ("", None, [], {}):
                    continue
                if field in {"local_pdf", "pdf_path", "pdf_url", "url", "doi", "arxiv_id"} or not existing.get(field):
                    existing[field] = value
            continue
        seen.add(key)
        by_key[key] = paper
        result.append(paper)
    return result


def paper_identity(paper: dict[str, Any]) -> str:
    for field in ("doi", "arxiv_id", "url", "pdf_url", "title", "local_pdf"):
        value = str(paper.get(field) or "").strip().lower()
        if value:
            return f"{field}:{' '.join(value.split())}"
    return ""


def render_ingest_report(
    papers: list[dict[str, Any]],
    notes: list[dict[str, str]],
    *,
    inbox_dir: Path,
    index_path: Path,
) -> str:
    rows = [
        [
            paper.get("title", ""),
            paper.get("year", ""),
            paper.get("source", ""),
            paper.get("local_pdf", "") or paper.get("pdf_url", "") or paper.get("url", "") or paper.get("doi", ""),
        ]
        for paper in papers
    ]
    note_rows = [[note.get("file", ""), note.get("status", ""), note.get("detail", "")] for note in notes]
    return "\n".join(
        [
            "# Paper Ingest Report",
            "",
            f"- generated_at: {timestamp()}",
            f"- inbox: `{inbox_dir}`",
            f"- output: `{index_path}`",
            f"- imported_papers: {len(papers)}",
            "",
            "## Imported Papers",
            "",
            md_table(["title", "year", "source", "locator"], rows) if rows else "No papers imported.",
            "",
            "## Files",
            "",
            md_table(["file", "status", "detail"], note_rows) if note_rows else "No files found.",
            "",
            "## Next",
            "",
            "```bash",
            "python3 -m idea_workbench run-deep <project>",
            "python3 -m idea_workbench research <project>",
            "```",
            "",
        ]
    )

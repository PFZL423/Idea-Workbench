from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import detail_report_path
from .render import md_table, timestamp


ARXIV_ABS_RE = re.compile(r"arxiv\.org/(?:abs|html)/([^?#\s]+)", re.IGNORECASE)
ARXIV_PDF_RE = re.compile(r"arxiv\.org/pdf/([^?#\s]+?)(?:\.pdf)?$", re.IGNORECASE)
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.([^/#?\s]+)", re.IGNORECASE)


@dataclass
class PdfFetchResult:
    papers: list[dict[str, Any]]
    report_path: Path
    index_path: Path


def run_pdf_fetch(
    project: Any,
    *,
    top: int = 10,
    dry_run: bool = False,
    force: bool = False,
    timeout: float = 60.0,
) -> PdfFetchResult:
    source_papers = load_paper_jsons(project.papers_dir)
    selected = source_papers[: max(top, 0)]
    pdf_dir = project.papers_dir / "pdfs"
    if not dry_run:
        pdf_dir.mkdir(parents=True, exist_ok=True)

    processed: list[dict[str, Any]] = []
    for paper in selected:
        item = dict(paper)
        existing_pdf = normalize_existing_pdf(item, project.root)
        pdf_url = resolve_pdf_url(item)
        if pdf_url and not item.get("pdf_url"):
            item["pdf_url"] = pdf_url

        if existing_pdf and existing_pdf.exists() and not force:
            item["local_pdf"] = str(existing_pdf)
            item["pdf_status"] = "exists"
            item["pdf_error"] = ""
            processed.append(item)
            continue

        if not pdf_url:
            item["pdf_status"] = "unresolved"
            item["pdf_error"] = "No direct PDF URL could be resolved."
            processed.append(item)
            continue

        target = pdf_dir / build_pdf_filename(item, pdf_url)
        if dry_run:
            item["local_pdf"] = str(target)
            item["pdf_status"] = "resolved"
            item["pdf_error"] = ""
            processed.append(item)
            continue

        if target.exists() and not force:
            item["local_pdf"] = str(target)
            item["pdf_status"] = "exists"
            item["pdf_error"] = ""
            processed.append(item)
            continue

        try:
            download_pdf(pdf_url, target, timeout=timeout)
            item["local_pdf"] = str(target)
            item["pdf_status"] = "downloaded"
            item["pdf_error"] = ""
        except Exception as exc:  # noqa: BLE001 - keep per-paper errors in report.
            item["local_pdf"] = ""
            item["pdf_status"] = "failed"
            item["pdf_error"] = str(exc)
        processed.append(item)

    index_path = project.papers_dir / "papers_with_pdfs.json"
    index_path.write_text(json.dumps(processed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = detail_report_path(project, "pdf_downloads.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_pdf_report(processed, top=top, dry_run=dry_run), encoding="utf-8")
    return PdfFetchResult(papers=processed, report_path=report_path, index_path=index_path)


def load_paper_jsons(papers_dir: Path) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for path in sorted(papers_dir.glob("*.json")):
        if path.name == "papers_with_pdfs.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
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


def normalize_existing_pdf(paper: dict[str, Any], project_root: Path) -> Path | None:
    raw = str(paper.get("local_pdf") or paper.get("pdf_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def resolve_pdf_url(paper: dict[str, Any]) -> str:
    direct = str(paper.get("pdf_url") or "").strip()
    if direct:
        return direct

    for field in ("url", "doi", "arxiv_id", "id"):
        value = str(paper.get(field) or "").strip()
        if not value:
            continue
        arxiv_id = extract_arxiv_id(value)
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return ""


def extract_arxiv_id(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""

    if stripped.lower().startswith("arxiv:"):
        return clean_arxiv_id(stripped.split(":", 1)[1])

    for pattern in (ARXIV_PDF_RE, ARXIV_ABS_RE, ARXIV_DOI_RE):
        match = pattern.search(stripped)
        if match:
            return clean_arxiv_id(match.group(1))

    if re.fullmatch(r"(?:[a-z-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", stripped, re.IGNORECASE):
        return clean_arxiv_id(stripped)
    return ""


def clean_arxiv_id(value: str) -> str:
    cleaned = value.strip().removesuffix(".pdf")
    return cleaned.strip("/")


def build_pdf_filename(paper: dict[str, Any], pdf_url: str) -> str:
    arxiv_id = extract_arxiv_id(pdf_url)
    if arxiv_id:
        return safe_filename(f"arxiv_{arxiv_id}") + ".pdf"
    title = str(paper.get("title") or "paper")
    year = str(paper.get("year") or str(paper.get("published_date", ""))[:4] or "").strip()
    stem = f"{year}_{title}" if year else title
    return safe_filename(stem) + ".pdf"


def safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    name = re.sub(r"_+", "_", name).strip("._")
    return (name or "paper")[:96]


def download_pdf(url: str, target: Path, *, timeout: float = 60.0) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "idea-workbench/0.2 (+local research assistant)",
            "Accept": "application/pdf,*/*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-requested paper URL.
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    if not looks_like_pdf(data, content_type, url):
        raise RuntimeError(f"downloaded content does not look like a PDF: content-type={content_type!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def looks_like_pdf(data: bytes, content_type: str, url: str) -> bool:
    if data.startswith(b"%PDF"):
        return True
    lowered_type = content_type.lower()
    return "pdf" in lowered_type and url.lower().endswith(".pdf")


def render_pdf_report(papers: list[dict[str, Any]], *, top: int, dry_run: bool) -> str:
    counts: dict[str, int] = {}
    for paper in papers:
        status = str(paper.get("pdf_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    lines = [
        "# PDF 获取报告",
        "",
        f"生成时间：{timestamp()}",
        "",
        f"- 考察论文数：{len(papers)} / top={top}",
        f"- 模式：{'dry-run，只解析不下载' if dry_run else '实际下载'}",
        f"- 状态统计：{', '.join(f'{key}={value}' for key, value in sorted(counts.items())) or '无'}",
        "",
        "## 明细",
        "",
    ]
    rows = []
    for paper in papers:
        rows.append(
            [
                paper.get("title", ""),
                paper.get("year", "") or str(paper.get("published_date", ""))[:4],
                paper.get("source", ""),
                paper.get("pdf_status", ""),
                paper.get("pdf_url", ""),
                paper.get("local_pdf", ""),
                paper.get("pdf_error", ""),
            ]
        )
    lines.append(md_table(["标题", "年份", "来源", "状态", "PDF URL", "本地 PDF", "错误"], rows))
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "下载成功后运行：",
            "",
            "```bash",
            "python3 -m idea_workbench evidence <project>",
            "```",
        ]
    )
    return "\n".join(lines).strip() + "\n"

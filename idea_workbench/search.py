from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


USER_AGENT = "idea-workbench/0.1 (local research assistant)"


def run_search(
    queries: list[dict[str, str]],
    *,
    sources: list[str],
    limit: int,
    offline: bool,
    timeout: float = 12.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    if offline:
        return [], ["offline mode: only queries were generated; no API search was attempted."]

    mcp_papers, mcp_errors = run_paper_search_mcp(queries, sources=sources, limit=limit)
    if mcp_papers:
        return dedupe_papers(mcp_papers), mcp_errors

    all_papers: list[dict[str, Any]] = []
    errors: list[str] = []
    errors.extend(mcp_errors)
    for query in queries:
        query_text = query["query"]
        for source in sources:
            try:
                if source == "arxiv":
                    papers = search_arxiv(query_text, limit=limit, timeout=timeout)
                elif source == "openalex":
                    papers = search_openalex(query_text, limit=limit, timeout=timeout)
                elif source == "semantic_scholar":
                    papers = search_semantic_scholar(query_text, limit=limit, timeout=timeout)
                else:
                    errors.append(f"unknown source skipped: {source}")
                    continue
                for paper in papers:
                    paper["query_id"] = query["id"]
                    paper["claim_id"] = query["claim_id"]
                    paper["query"] = query_text
                all_papers.extend(papers)
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - CLI should keep partial evidence.
                errors.append(f"{source} failed for {query['id']}: {exc}")

    return dedupe_papers(all_papers), errors


def run_paper_search_mcp(
    queries: list[dict[str, str]],
    *,
    sources: list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    repo = resolve_paper_search_mcp_repo()
    if repo is None:
        return [], ["paper-search-mcp unavailable; falling back to built-in search adapters."]

    all_papers: list[dict[str, Any]] = []
    errors: list[str] = []
    mapped_sources = ",".join(map_source_for_paper_search(source) for source in sources)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")

    for query in queries:
        cmd = [
            sys.executable,
            "-m",
            "paper_search_mcp.cli",
            "search",
            query["query"],
            "-n",
            str(limit),
            "-s",
            mapped_sources,
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - optional adapter.
            errors.append(f"paper-search-mcp failed for {query['id']}: {exc}")
            continue

        if result.returncode != 0:
            errors.append(f"paper-search-mcp failed for {query['id']}: {result.stderr.strip() or result.stdout.strip()}")
            continue

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            errors.append(f"paper-search-mcp returned non-JSON for {query['id']}: {exc}")
            continue

        for paper in payload.get("papers", []):
            normalized = normalize_paper_search_result(paper)
            normalized["query_id"] = query["id"]
            normalized["claim_id"] = query["claim_id"]
            normalized["query"] = query["query"]
            all_papers.append(normalized)
        source_errors = payload.get("errors") or {}
        for source, message in source_errors.items():
            if message:
                errors.append(f"paper-search-mcp {source} for {query['id']}: {message}")

    return all_papers, errors


def resolve_paper_search_mcp_repo() -> Path | None:
    candidates: list[Path] = []
    if os.environ.get("PAPER_SEARCH_MCP_REPO"):
        candidates.append(Path(os.environ["PAPER_SEARCH_MCP_REPO"]).expanduser())
    package_root = Path(__file__).resolve().parents[1]
    toolbox_root = Path(__file__).resolve().parents[2]
    candidates.append(package_root / "third_party" / "paper-search-mcp")
    candidates.append(toolbox_root / "10_literature_and_rag" / "paper-search-mcp")
    for candidate in candidates:
        if (candidate / "paper_search_mcp" / "cli.py").exists():
            return candidate.resolve()
    return None


def map_source_for_paper_search(source: str) -> str:
    mapping = {
        "semantic_scholar": "semantic",
        "semanticscholar": "semantic",
    }
    return mapping.get(source, source)


def normalize_paper_search_result(paper: dict[str, Any]) -> dict[str, Any]:
    year = ""
    published = str(paper.get("published_date") or "")
    if len(published) >= 4:
        year = published[:4]
    return {
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", ""),
        "authors": paper.get("authors", ""),
        "year": year,
        "venue": paper.get("venue", "") or paper.get("source", ""),
        "url": paper.get("url", "") or paper.get("pdf_url", ""),
        "doi": paper.get("doi", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "source": paper.get("source", "paper-search-mcp"),
        "citations": paper.get("citations", 0),
    }


def fetch_text(url: str, *, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - public research APIs only.
        return response.read().decode("utf-8", errors="replace")


def search_arxiv(query: str, *, limit: int, timeout: float) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    text = fetch_text(url, timeout=timeout)
    root = ET.fromstring(text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        title = node_text(entry, "atom:title", ns)
        abstract = node_text(entry, "atom:summary", ns)
        published = node_text(entry, "atom:published", ns)
        authors = [normalize_space(author.findtext("atom:name", default="", namespaces=ns)) for author in entry.findall("atom:author", ns)]
        link = ""
        for link_node in entry.findall("atom:link", ns):
            if link_node.attrib.get("rel") == "alternate":
                link = link_node.attrib.get("href", "")
                break
        papers.append(
            {
                "title": normalize_space(title),
                "abstract": normalize_space(abstract),
                "authors": [author for author in authors if author],
                "year": published[:4],
                "venue": "arXiv",
                "url": link,
                "source": "arxiv",
            }
        )
    return papers


def search_openalex(query: str, *, limit: int, timeout: float) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"search": query, "per-page": limit})
    url = f"https://api.openalex.org/works?{params}"
    data = json.loads(fetch_text(url, timeout=timeout))
    papers: list[dict[str, Any]] = []
    for item in data.get("results", []):
        authors = [
            authorship.get("author", {}).get("display_name", "")
            for authorship in item.get("authorships", [])
        ]
        abstract = inverted_index_to_text(item.get("abstract_inverted_index") or {})
        venue = (
            item.get("primary_location", {})
            .get("source", {})
            .get("display_name", "")
        )
        papers.append(
            {
                "title": normalize_space(item.get("title") or item.get("display_name") or ""),
                "abstract": normalize_space(abstract),
                "authors": [author for author in authors if author],
                "year": item.get("publication_year", ""),
                "venue": venue,
                "url": item.get("doi") or item.get("id") or "",
                "source": "openalex",
            }
        )
    return papers


def search_semantic_scholar(query: str, *, limit: int, timeout: float) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": limit,
            "fields": "title,abstract,authors,year,url,venue,externalIds",
        }
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    data = json.loads(fetch_text(url, timeout=timeout))
    papers: list[dict[str, Any]] = []
    for item in data.get("data", []):
        papers.append(
            {
                "title": normalize_space(item.get("title", "")),
                "abstract": normalize_space(item.get("abstract") or ""),
                "authors": [author.get("name", "") for author in item.get("authors", []) if author.get("name")],
                "year": item.get("year", ""),
                "venue": item.get("venue", ""),
                "url": item.get("url", ""),
                "source": "semantic_scholar",
            }
        )
    return papers


def node_text(entry: ET.Element, path: str, ns: dict[str, str]) -> str:
    node = entry.find(path, ns)
    return node.text if node is not None and node.text else ""


def inverted_index_to_text(index: dict[str, list[int]]) -> str:
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        for position in positions:
            words.append((position, word))
    return " ".join(word for _position, word in sorted(words))


def normalize_space(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for paper in papers:
        title = normalize_space(str(paper.get("title", ""))).lower()
        if not title or title in seen:
            continue
        seen.add(title)
        deduped.append(paper)
    return deduped

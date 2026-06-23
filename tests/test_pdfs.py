from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from idea_workbench.pdfs import extract_arxiv_id, resolve_pdf_url


REPO_ROOT = Path(__file__).resolve().parents[1]


class PdfFetchTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "idea_workbench", *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_arxiv_pdf_resolution(self) -> None:
        self.assertEqual(extract_arxiv_id("https://arxiv.org/abs/2506.14186v2"), "2506.14186v2")
        self.assertEqual(
            resolve_pdf_url({"url": "https://arxiv.org/abs/2506.14186v2"}),
            "https://arxiv.org/pdf/2506.14186v2.pdf",
        )
        self.assertEqual(
            resolve_pdf_url({"doi": "10.48550/arxiv.2403.08716"}),
            "https://arxiv.org/pdf/2403.08716.pdf",
        )

    def test_pdfs_dry_run_writes_index_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world model")
            papers = [
                {
                    "title": "Differentiable Simulation of Hard Contacts",
                    "year": 2025,
                    "source": "arxiv",
                    "url": "https://arxiv.org/abs/2506.14186v2",
                },
                {
                    "title": "Non PDF Paper",
                    "year": 2024,
                    "source": "manual",
                    "url": "https://example.com/not-a-pdf",
                },
            ]
            (project / "papers" / "api_papers.json").write_text(json.dumps(papers), encoding="utf-8")

            self.run_cli("pdfs", str(project), "--top", "2", "--dry-run")

            index = json.loads((project / "papers" / "papers_with_pdfs.json").read_text(encoding="utf-8"))
            self.assertEqual(index[0]["pdf_status"], "resolved")
            self.assertEqual(index[0]["pdf_url"], "https://arxiv.org/pdf/2506.14186v2.pdf")
            self.assertEqual(index[1]["pdf_status"], "unresolved")
            report = (project / "reports" / "details" / "pdf_downloads.md").read_text(encoding="utf-8")
            self.assertIn("PDF 获取报告", report)

    def test_existing_local_pdf_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world model")
            pdf = project / "papers" / "manual.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            papers = [
                {
                    "title": "Manual PDF",
                    "year": 2026,
                    "source": "manual",
                    "local_pdf": str(pdf),
                }
            ]
            (project / "papers" / "manual_papers.json").write_text(json.dumps(papers), encoding="utf-8")

            self.run_cli("pdfs", str(project), "--top", "1")

            index = json.loads((project / "papers" / "papers_with_pdfs.json").read_text(encoding="utf-8"))
            self.assertEqual(index[0]["pdf_status"], "exists")
            self.assertEqual(index[0]["local_pdf"], str(pdf))


if __name__ == "__main__":
    unittest.main()

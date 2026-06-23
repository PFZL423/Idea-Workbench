from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from idea_workbench.llm_workflow import load_project_papers
from idea_workbench.project import get_project


REPO_ROOT = Path(__file__).resolve().parents[1]


class IngestTest(unittest.TestCase):
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

    def test_ingest_imports_low_friction_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "idea"
            self.run_cli("init", str(project_root), "--seed-text", "world model")
            inbox = project_root / "papers" / "inbox"
            (inbox / "contact_gradient_atlas.pdf").write_bytes(b"%PDF-1.4\n")
            (inbox / "arxiv.txt").write_text("https://arxiv.org/abs/2506.14186v2\n", encoding="utf-8")
            (inbox / "doi.txt").write_text("10.48550/arxiv.2403.08716\n", encoding="utf-8")
            (inbox / "refs.bib").write_text(
                """
@inproceedings{test2025,
  title = {Prediction Control Gap Benchmark},
  author = {Ada Example},
  year = {2025},
  url = {https://example.com/prediction-control-gap}
}
""",
                encoding="utf-8",
            )

            result = self.run_cli("ingest", str(project_root))

            self.assertIn("papers: 4", result.stdout)
            imported = json.loads((project_root / "papers" / "imported_papers.json").read_text(encoding="utf-8"))
            self.assertEqual(len(imported), 4)
            self.assertTrue(any(paper.get("source") == "manual_pdf" for paper in imported))
            self.assertTrue(any(paper.get("arxiv_id") == "2506.14186v2" for paper in imported))
            self.assertTrue(any(paper.get("doi") == "10.48550/arxiv.2403.08716" for paper in imported))
            self.assertTrue(any(paper.get("title") == "Prediction Control Gap Benchmark" for paper in imported))
            self.assertTrue((project_root / "reports" / "details" / "ingest.md").exists())

    def test_imported_and_api_papers_share_project_paper_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "idea"
            self.run_cli("init", str(project_root), "--seed-text", "world model")
            inbox = project_root / "papers" / "inbox"
            (inbox / "manual.pdf").write_bytes(b"%PDF-1.4\n")
            self.run_cli("ingest", str(project_root))
            (project_root / "papers" / "api_papers.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Retrieved API Paper",
                            "source": "arxiv",
                            "url": "https://arxiv.org/abs/2501.00001",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            papers = load_project_papers(get_project(project_root))

            titles = {paper.get("title") for paper in papers}
            self.assertIn("manual", titles)
            self.assertIn("Retrieved API Paper", titles)


if __name__ == "__main__":
    unittest.main()

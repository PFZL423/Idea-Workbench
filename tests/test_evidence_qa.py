from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class EvidenceQaTest(unittest.TestCase):
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

    def make_project_with_claims_and_papers(self, tmp: str) -> Path:
        project = Path(tmp) / "idea"
        self.run_cli("init", str(project), "--seed-text", "world action model")
        claims = {
            "claims": [
                {
                    "id": "C1",
                    "claim": "Action-conditioned world models expose controllable factors.",
                    "equivalent_terms": ["affordance learning", "controllable representation"],
                }
            ],
            "risk_questions": [],
        }
        papers = [
            {
                "title": "A Paper About Action-Conditioned World Models",
                "year": "2025",
                "source": "manual",
                "pdf_url": "https://example.com/paper.pdf",
                "url": "https://example.com/paper",
            }
        ]
        (project / "state" / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
        (project / "papers" / "manual_papers.json").write_text(json.dumps(papers), encoding="utf-8")
        return project

    def test_evidence_mock_generates_claim_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project_with_claims_and_papers(tmp)
            self.run_cli("evidence", str(project), "--mock")
            report = (project / "reports" / "details" / "evidence_qa.md").read_text(encoding="utf-8")
            self.assertIn("Evidence QA Report", report)
            self.assertIn("MOCK", report)
            jsonl = (project / "evidence" / "claim_evidence.jsonl").read_text(encoding="utf-8")
            self.assertIn("C1", jsonl)

    def test_evidence_without_backend_degrades_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project_with_claims_and_papers(tmp)
            self.run_cli("evidence", str(project))
            status = json.loads((project / "evidence" / "evidence_status.json").read_text(encoding="utf-8"))
            self.assertIn(status["status"], {"unavailable", "needs_pdf_download", "no_pdf", "ok"})
            self.assertTrue((project / "reports" / "details" / "evidence_qa.md").exists())


if __name__ == "__main__":
    unittest.main()

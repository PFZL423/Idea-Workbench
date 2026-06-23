from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTest(unittest.TestCase):
    def run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "idea_workbench", *args],
            cwd=cwd or REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_offline_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for contact-rich robot manipulation")
            self.run_cli("run-all", str(project), "--offline")

            expected = [
                project / "reports" / "details" / "decomposition.md",
                project / "queries.yaml",
                project / "reports" / "details" / "search_log.md",
                project / "reports" / "details" / "novelty_matrix.md",
                project / "reports" / "details" / "refined_ideas.md",
                project / "reports" / "details" / "experiment_plan.md",
                project / "reports" / "final_report_cn.md",
            ]
            for path in expected:
                self.assertTrue(path.exists(), path)

            report = (project / "reports" / "final_report_cn.md").read_text(encoding="utf-8")
            self.assertIn("科研 Idea Workbench 总报告", report)
            self.assertIn("最小实验计划", report)

    def test_manual_paper_affects_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world model for robot manipulation planning")
            self.run_cli("decompose", str(project))
            manual = [
                {
                    "title": "World Models for Robot Manipulation Planning",
                    "abstract": "A world model for robot manipulation planning with representation learning and model-based reinforcement learning.",
                    "year": 2025,
                    "venue": "Manual Test",
                    "url": "https://example.com/paper",
                    "source": "manual",
                }
            ]
            (project / "papers" / "manual_papers.json").write_text(json.dumps(manual), encoding="utf-8")
            self.run_cli("matrix", str(project))
            matrix = json.loads((project / "state" / "novelty_matrix.json").read_text(encoding="utf-8"))
            self.assertTrue(matrix["rows"])
            self.assertTrue(any(row["evidence_count"] > 0 for row in matrix["rows"]))


if __name__ == "__main__":
    unittest.main()

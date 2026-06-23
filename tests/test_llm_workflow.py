from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LlmWorkflowTest(unittest.TestCase):
    def run_cli(self, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "idea_workbench", *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def run_cli_no_check(self, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", "idea_workbench", *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_doctor_reports_missing_gpt_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model")
            result = self.run_cli(
                "doctor",
                str(project),
                env_extra={"GPT_API_BASE_URL": "", "GPT_API_KEY": ""},
            )
            self.assertIn("GPT_API_BASE_URL", result.stdout)
            self.assertIn("missing", result.stdout)

    def test_run_deep_dry_run_writes_prompts_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model")
            self.run_cli(
                "run-deep",
                str(project),
                "--dry-run",
                env_extra={"GPT_API_BASE_URL": "", "GPT_API_KEY": ""},
            )
            self.assertTrue((project / "reports" / "details" / "run_deep_dry_run.md").exists())
            self.assertTrue((project / "traces" / "dry_run_prompts.json").exists())

    def test_run_deep_mock_llm_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for robot manipulation")
            self.run_cli(
                "run-deep",
                str(project),
                "--offline-search",
                env_extra={"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"},
            )
            expected = [
                project / "state" / "brief.json",
                project / "state" / "claims.json",
                project / "state" / "reviewer_report.json",
                project / "reports" / "details" / "research_brief.md",
                project / "reports" / "details" / "reviewer_report.md",
                project / "reports" / "final_report_cn.md",
                project / "traces" / "llm_calls.jsonl",
            ]
            for path in expected:
                self.assertTrue(path.exists(), path)
            final_report = (project / "reports" / "final_report_cn.md").read_text(encoding="utf-8")
            self.assertIn("Adversarial Reviewer Report", final_report)
            self.assertIn("Controllability-aware WAM", final_report)

    def test_secrets_local_yaml_supplies_model_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model")
            (project / "secrets.local.yaml").write_text(
                """
model_tiers:
  cheap:
    base_url: mock://idea-workbench
    api_key: mock-key
  standard:
    base_url: mock://idea-workbench
    api_key: mock-key
  strong:
    base_url: mock://idea-workbench
    api_key: mock-key
  frontier:
    base_url: mock://idea-workbench
    api_key: mock-key
""".strip()
                + "\n",
                encoding="utf-8",
            )
            result = self.run_cli(
                "doctor",
                str(project),
                env_extra={"GPT_API_BASE_URL": "", "GPT_API_KEY": ""},
            )
            self.assertIn("config", result.stdout)
            self.assertIn("ready", result.stdout)
            self.run_cli(
                "run-deep",
                str(project),
                "--offline-search",
                env_extra={"GPT_API_BASE_URL": "", "GPT_API_KEY": ""},
            )
            self.assertTrue((project / "reports" / "final_report_cn.md").exists())

    def test_run_deep_uses_manual_local_papers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for tactile manipulation")
            (project / "config.yaml").write_text(
                (project / "config.yaml").read_text(encoding="utf-8")
                + "\nevidence_qa:\n  mock: true\n",
                encoding="utf-8",
            )
            pdf_dir = project / "papers" / "pdfs"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            (pdf_dir / "manual.pdf").write_bytes(b"%PDF-1.4\n")
            manual_papers = [
                {
                    "title": "Manual Related Work on Tactile World Models",
                    "year": 2026,
                    "source": "manual",
                    "local_pdf": "papers/pdfs/manual.pdf",
                }
            ]
            (project / "papers" / "manual_papers.json").write_text(json.dumps(manual_papers), encoding="utf-8")
            self.run_cli(
                "run-deep",
                str(project),
                "--offline-search",
                env_extra={"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"},
            )
            evidence_report = (project / "reports" / "details" / "evidence_qa.md").read_text(encoding="utf-8")
            self.assertIn("Manual Related Work on Tactile World Models", evidence_report)
            self.assertIn(str(pdf_dir / "manual.pdf"), evidence_report)
            self.assertTrue((project / "state" / "run_deep_stages" / "novelty_matrix_v2_batch_1.json").exists())
            novelty_prompts = list((project / "traces").glob("novelty_matrix_builder_batch_*.prompt.md"))
            self.assertTrue(novelty_prompts)
            prompt_text = novelty_prompts[0].read_text(encoding="utf-8")
            self.assertIn("evidence_contexts", prompt_text)
            self.assertNotIn('"papers": [', prompt_text)

    def test_idea_search_mock_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for robot manipulation")
            env = {"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"}
            self.run_cli("run-deep", str(project), "--offline-search", env_extra=env)
            self.run_cli("idea-search", str(project), "--branches", "8", "--shortlist", "3", "--final", "2", env_extra=env)

            report = (project / "reports" / "idea_search.md").read_text(encoding="utf-8")
            state = json.loads((project / "state" / "idea_search.json").read_text(encoding="utf-8"))
            self.assertIn("Idea Search Report", report)
            self.assertIn("Controllability Probe Suite", report)
            self.assertEqual(state["parameters"]["branches"], 8)
            self.assertIn("literature_store", state)
            self.assertTrue((project / "state" / "literature_store.json").exists())
            self.assertTrue((project / "reports" / "details" / "literature_store.md").exists())
            self.assertTrue((project / "state" / "idea_search_stages" / "branches_8_batch_1.json").exists())
            self.assertTrue(state["final"]["final_ideas"])

    def test_research_mock_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for robot manipulation")
            env = {"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"}
            self.run_cli("run-deep", str(project), "--offline-search", env_extra=env)
            self.run_cli("research", str(project), "--ideas", "4", "--final", "2", env_extra=env)

            report = (project / "reports" / "research.md").read_text(encoding="utf-8")
            rounds = (project / "reports" / "details" / "research_rounds.md").read_text(encoding="utf-8")
            state = json.loads((project / "state" / "research_workflow.json").read_text(encoding="utf-8"))
            self.assertIn("闭环 Research Workflow 报告", report)
            self.assertIn("WAM", report)
            self.assertIn("Critic Panel", rounds)
            self.assertEqual(state["parameters"]["ideas"], 4)
            self.assertTrue(state["final"]["final_ideas"])
            self.assertTrue((project / "state" / "research_stages" / "critic_panel.json").exists())

    def test_idea_search_dry_run_writes_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model for robot manipulation")
            env = {"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"}
            self.run_cli("run-deep", str(project), "--offline-search", env_extra=env)
            self.run_cli(
                "idea-search",
                str(project),
                "--branches",
                "6",
                "--shortlist",
                "2",
                "--final",
                "1",
                "--dry-run",
                env_extra={"GPT_API_BASE_URL": "", "GPT_API_KEY": ""},
            )

            self.assertTrue((project / "reports" / "details" / "idea_search_dry_run.md").exists())
            prompt_path = project / "traces" / "idea_search_dry_run_prompts.json"
            self.assertTrue(prompt_path.exists())
            prompts = prompt_path.read_text(encoding="utf-8")
            self.assertIn("evidence_items", prompts)
            self.assertIn("paper_passages", prompts)
            self.assertNotIn('"papers": [', prompts)

    def test_idea_search_requires_run_deep_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "idea"
            self.run_cli("init", str(project), "--seed-text", "world action model")
            result = self.run_cli_no_check(
                "idea-search",
                str(project),
                env_extra={"GPT_API_BASE_URL": "mock://idea-workbench", "GPT_API_KEY": "mock-key"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires run-deep artifacts", result.stderr)


if __name__ == "__main__":
    unittest.main()

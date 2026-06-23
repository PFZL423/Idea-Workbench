from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea_workbench.literature_store import literature_store_signature, retrieve_stage_context
from idea_workbench.llm_workflow import cached_stage
from idea_workbench.project import init_project


class LiteratureStoreTest(unittest.TestCase):
    def test_stage_retrieval_returns_relevant_passages(self) -> None:
        store = {
            "paper_count": 1,
            "passage_count": 1,
            "evidence_count": 1,
            "papers": [
                {
                    "paper_id": "P-test",
                    "title": "Prior Work on Controllable World Models",
                    "year": "2025",
                    "source": "manual",
                    "url": "https://example.com",
                    "pdf_url": "",
                    "local_pdf": "papers/pdfs/prior.pdf",
                    "has_local_pdf": True,
                }
            ],
            "passages": [
                {
                    "id": "PASS-P-test-1-1",
                    "paper_id": "P-test",
                    "title": "Prior Work on Controllable World Models",
                    "page": 3,
                    "local_pdf": "papers/pdfs/prior.pdf",
                    "text": "The main limitation is that prediction accuracy does not guarantee controllable action effects.",
                    "tags": ["failure_signal", "benchmark_signal"],
                }
            ],
            "evidence_items": [
                {
                    "id": "E-test",
                    "type": "overlap_risk",
                    "paper_id": "P-test",
                    "title": "Prior Work on Controllable World Models",
                    "text": "This paper may overlap with action-conditioned controllability claims.",
                    "source": "novelty_matrix",
                }
            ],
        }
        base_context = {
            "brief": {"topic": "controllable world model", "problem_statement": "prediction vs control"},
            "claims": {"claims": [{"id": "C1", "claim": "world model controllability"}]},
            "novelty_matrix": {"rows": []},
            "reviewer_report": {},
        }

        context = retrieve_stage_context(store=store, stage="bottleneck_extractor", base_context=base_context)

        self.assertEqual(context["evidence_summary"]["selected_passages"], 1)
        self.assertEqual(context["paper_passages"][0]["paper_id"], "P-test")
        self.assertIn("prediction accuracy", context["paper_passages"][0]["text"])

    def test_signature_changes_when_pdf_or_abstract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = init_project(Path(tmp) / "idea")
            pdf_dir = project.papers_dir / "pdfs"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = pdf_dir / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 first\n")
            paper = {
                "title": "A Local Paper",
                "abstract": "first abstract",
                "local_pdf": "papers/pdfs/paper.pdf",
            }

            first = literature_store_signature(
                project=project,
                papers=[paper],
                novelty_matrix={},
                reviewer_report={},
                evidence_qa={},
            )
            pdf_path.write_bytes(b"%PDF-1.4 second\n")
            second = literature_store_signature(
                project=project,
                papers=[paper],
                novelty_matrix={},
                reviewer_report={},
                evidence_qa={},
            )
            changed_abstract = dict(paper)
            changed_abstract["abstract"] = "second abstract"
            third = literature_store_signature(
                project=project,
                papers=[changed_abstract],
                novelty_matrix={},
                reviewer_report={},
                evidence_qa={},
            )

            self.assertNotEqual(first["papers"][0]["pdf_file"]["sha256"], second["papers"][0]["pdf_file"]["sha256"])
            self.assertNotEqual(second["papers"][0]["abstract_hash"], third["papers"][0]["abstract_hash"])

    def test_cached_stage_recomputes_when_input_hash_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stage.json"
            calls = {"count": 0}

            def producer() -> dict[str, int]:
                calls["count"] += 1
                return {"value": calls["count"]}

            first = cached_stage(path, {"input": 1}, producer)
            second = cached_stage(path, {"input": 1}, producer)
            third = cached_stage(path, {"input": 2}, producer)

            self.assertEqual(first, {"value": 1})
            self.assertEqual(second, {"value": 1})
            self.assertEqual(third, {"value": 2})


if __name__ == "__main__":
    unittest.main()

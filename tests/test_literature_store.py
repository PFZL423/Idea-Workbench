from __future__ import annotations

import unittest

from idea_workbench.literature_store import retrieve_stage_context


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


if __name__ == "__main__":
    unittest.main()

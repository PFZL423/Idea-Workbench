from __future__ import annotations

import unittest

from idea_workbench.search import resolve_paper_search_mcp_repo


class SearchBackendTest(unittest.TestCase):
    def test_vendored_paper_search_mcp_is_discoverable(self) -> None:
        repo = resolve_paper_search_mcp_repo()
        self.assertIsNotNone(repo)
        assert repo is not None
        self.assertTrue((repo / "paper_search_mcp" / "cli.py").exists())
        self.assertEqual(repo.name, "paper-search-mcp")


if __name__ == "__main__":
    unittest.main()

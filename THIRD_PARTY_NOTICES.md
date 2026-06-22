# Third-Party Notices

This repository vendors a small third-party package so that `idea-workbench` can run from a plain `git clone` without requiring a separate checkout.

## paper-search-mcp

- Path: `third_party/paper-search-mcp`
- Upstream: `https://github.com/openags/paper-search-mcp`
- Vendored commit: `d438222`
- License: MIT License
- License file: `third_party/paper-search-mcp/LICENSE`
- Vendored subset: runtime source package, `pyproject.toml`, upstream `README.md`, and upstream `LICENSE`

`idea-workbench` uses this package as an optional literature-search backend. If it is unavailable, the CLI falls back to built-in public API adapters for arXiv, OpenAlex, and Semantic Scholar.

The upstream MIT license text is preserved in the vendored directory. Do not remove it when redistributing this repository.

## Runtime Dependencies Not Vendored

PaperQA2 is not vendored here. It is an optional runtime dependency installed from PyPI:

```bash
python3 -m pip install paper-qa
```

The local CLI detects PaperQA2 / `pqa` at runtime and degrades cleanly when it is unavailable.

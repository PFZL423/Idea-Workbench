# Idea Workbench

本工具是一个本地 CLI + 文件库形式的科研 idea 打磨工具。它不是自动科研 agent，也不声称能证明一个想法新颖；它的目标是把一个粗糙想法拆成可检索 claim，生成查重 query，整理 novelty matrix，并给出几个更清晰的 idea 版本和最小实验计划。

v0.2 开始支持 LLM 编排版：通过第三方 GPT-compatible 中转站调用不同档位模型，让便宜模型处理 query/粗筛，让高档模型处理 novelty、review 和 idea refinement。无 key 时不会假装运行 LLM，只能跑离线流程或 `--dry-run`。

## 适合场景

- 你有一个不成熟的科研想法，但不确定有没有人做过。
- 你想知道这个想法和 world model、具身智能、WAM、CILD、robot learning 等邻近工作有什么差异。
- 你想先得到一个“该查什么、哪里风险高、最小实验怎么设计”的工作包。

## 安装与运行

在工具目录下可以直接运行：

```bash
python -m idea_workbench --help
```

也可以安装为本地命令：

```bash
python -m pip install -e .
idea-workbench --help
```

## 推荐流程

```bash
idea-workbench init my-idea
```

编辑 `my-idea/seed.md`，写入你的粗糙想法。然后运行：

```bash
idea-workbench run-all my-idea --offline
```

`--offline` 表示只生成 query 和报告骨架，不访问论文 API。若希望尝试公开 API：

```bash
idea-workbench run-all my-idea --limit 3
```

生成结果在：

- `reports/decomposition.md`：idea 拆解和可检索 claims
- `queries.yaml`：检索 query
- `reports/search_log.md`：检索记录
- `reports/novelty_matrix.md`：查重风险矩阵
- `reports/refined_ideas.md`：打磨后的候选 idea
- `reports/experiment_plan.md`：最小实验计划
- `reports/final_report_cn.md`：中文总报告

## LLM 深度流程

先检查环境：

```bash
python -m idea_workbench doctor my-idea
```

推荐用项目本地配置文件，不需要每次在 shell 里 export。初始化项目后复制模板：

```bash
cp my-idea/secrets.local.yaml.example my-idea/secrets.local.yaml
```

然后编辑 `my-idea/secrets.local.yaml`：

```yaml
model_tiers:
  cheap:
    # DeepSeek 官方 OpenAI-compatible endpoint
    base_url: https://api.deepseek.com/v1
    api_key: sk-your-deepseek-key

  standard:
    base_url: https://your-gpt-relay.example.com/v1
    api_key: your-relay-key

  strong:
    base_url: https://your-gpt-relay.example.com/v1
    api_key: your-relay-key

  frontier:
    base_url: https://your-gpt-relay.example.com/v1
    api_key: your-relay-key
```

`secrets.local.yaml` 会被项目 `.gitignore` 忽略。`doctor` 会显示 URL/key 是否存在以及来源，但不会打印 key 内容。

也可以继续用环境变量：

```bash
export GPT_API_BASE_URL="https://your-relay.example.com/v1"
export GPT_API_KEY="your-relay-key"
```

默认模型档位：

```yaml
cheap: deepseek-chat
standard: gpt-5.4
strong: gpt-5.5, reasoning_effort=high
frontier: gpt-5.5, reasoning_effort=xhigh
```

运行完整 LLM 流程：

```bash
python -m idea_workbench run-deep my-idea
```

如果暂时没有 key，只想检查 prompt 和输入：

```bash
python -m idea_workbench run-deep my-idea --dry-run
```

如果想先跳过联网检索，只验证 LLM 编排：

```bash
python -m idea_workbench run-deep my-idea --offline-search
```

新增输出：

- `reports/research_brief.md`：LLM 提取的研究 brief
- `state/claims.json`：结构化 claims
- `reports/evidence_qa.md`：PDF/evidence QA 状态和 claim 证据
- `evidence/claim_evidence.jsonl`：机器可读 claim-level evidence
- `reports/reviewer_report.md`：高档模型审稿式批判
- `traces/llm_calls.jsonl`：每次模型调用的 trace
- `traces/*.prompt.md`：对应调用的 prompt

## Evidence QA / PaperQA2

工具已经接入可选的 PaperQA2/PDF evidence QA adapter。当前环境没装 PaperQA2 时不会影响主流程，`doctor` 会提示 unavailable，`run-deep` 会生成降级版 `reports/evidence_qa.md`。

先从检索结果里解析/下载 PDF：

```bash
python -m idea_workbench pdfs my-idea --top 10
```

只看哪些能解析，不下载：

```bash
python -m idea_workbench pdfs my-idea --top 10 --dry-run
```

单独运行：

```bash
python -m idea_workbench evidence my-idea
```

测试或验证报告格式：

```bash
python -m idea_workbench evidence my-idea --mock
```

Evidence QA 读取：

- `state/claims.json` 或 `state/decomposition.json`
- `papers/*.json` 中的 `local_pdf`、`pdf_path` 或 `pdf_url`
- `papers/papers_with_pdfs.json`，由 `pdfs` 命令生成

输出：

- `papers/pdfs/*.pdf`
- `reports/pdf_downloads.md`
- `evidence/claim_evidence.jsonl`
- `evidence/evidence_status.json`
- `reports/evidence_qa.md`

目前 adapter 优先处理已有本地 PDF；`pdfs` 命令会自动处理 arXiv 链接和已有 `pdf_url`，其他出版社网页仍可能需要人工补 PDF。

## 人工补充论文

如果 API 不可用，或你手动找到了论文，可以新建 `papers/manual_papers.json`：

```json
[
  {
    "title": "Paper title",
    "abstract": "Short abstract or your note",
    "authors": ["Author A", "Author B"],
    "year": 2025,
    "venue": "Conference or journal",
    "url": "https://example.com",
    "source": "manual"
  }
]
```

然后重新运行：

```bash
idea-workbench matrix my-idea
idea-workbench refine my-idea
idea-workbench experiment-plan my-idea
idea-workbench report my-idea
```

## 设计边界

- LLM 或启发式逻辑只能辅助拆解、生成 query、组织论证，不能替代人工读 paper。
- `Novelty Matrix` 的高/中/低风险是初筛优先级，不是新颖性结论。
- 第一版不自动运行 Genesis、CILD 或其他实验代码，只生成最小实验计划。
- LLM key 可从项目 `secrets.local.yaml`、`config.yaml` 或环境变量读取；`doctor` 和 trace 不会打印 key 内容。
- ChatGPT Plus 订阅不能直接作为 API key；本工具使用 GPT-compatible API 中转或兼容服务。
- 后续如果 CLI 被证明有用，可以升级成 Codex skill 或 MCP server。

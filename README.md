# Idea Workbench

`idea-workbench` 是一个本地科研 idea 打磨工具。它的目标不是自动发论文，而是把一个粗糙想法变成可检查的工作包：拆解 claim、检索文献、读本地 PDF 证据、生成 novelty matrix、给出审稿式批判、打磨候选 idea 和最小实验计划。

它适合这种场景：你已经有一个不成熟想法，也有一些相关论文，想先判断“有没有人做过、哪里最危险、下一步该怎么查和怎么实验”。

第一次使用时，先按这条主线走：

```text
写 seed.md → 配好 API key → doctor 检查 → 可选 ingest 文献 → run-deep 生成 evidence pack → research 生成最终 proposal
```

`idea-search` 是可选的扩展工具：当你想主动生成很多分支、再筛选少数方向时再跑它。只想得到一份主 research proposal 时，通常先跑 `run-deep`，再跑 `research` 就够了。

## 推荐主流程

### 1. Clone 并安装

```bash
git clone https://github.com/PFZL423/Idea-Workbench.git
cd Idea-Workbench
python3 -m pip install -e .
```

安装可选的 Evidence QA 后端：

```bash
python3 -m pip install paper-qa
```

`paper-qa` 提供 PaperQA2 / `pqa`。没有它也能跑主流程，但不会真正读 PDF 做 evidence QA。

### 2. 创建 idea 项目

```bash
python3 -m idea_workbench init my-idea
```

编辑：

```text
my-idea/seed.md
```

建议写清：

- 你想解决的问题
- 直觉上的方法
- 目标任务或实验环境
- 担心已有工作覆盖的部分
- 希望工具重点查的点

### 3. 可选：低门槛导入你已经知道的相关文献

这一步不是必需的。你没有现成论文时，可以先跳过，`run-deep` 会根据 seed 自动检索。

如果你已经知道几篇关键论文，建议手动导入；这通常会提高 novelty 检查和 reviewer critique 的质量。

最简单方式：把文件丢进 inbox。

```text
my-idea/papers/inbox/
```

支持：

```text
*.pdf          # 本地 PDF，标题先从文件名生成
*.bib          # BibTeX，解析 title/year/authors/url/doi
arxiv.txt      # 一行一个 arXiv ID / URL
doi.txt        # 一行一个 DOI
urls.txt       # 一行一个论文 URL
```

然后运行：

```bash
python3 -m idea_workbench ingest my-idea
```

它会生成：

```text
my-idea/papers/imported_papers.json
my-idea/reports/details/ingest.md
```

这些导入的论文会和 `run-deep` 自动搜索到的论文混在同一个 paper pool / literature store 里。后续 RAG 检索会统一使用它们，但仍保留 `source` 字段，例如 `manual_pdf`、`manual_bibtex`、`manual_arxiv`、`arxiv`。

有本地 PDF 的论文会保留 `local_pdf`，后续 Evidence QA 和 literature store 可以抽取接近原文的 passage；只有 DOI / URL / metadata 的论文不能保证读到全文。

如果你想手写更精确的元数据，也可以继续使用 `manual_papers.json`。推荐把 PDF 放到：

```text
my-idea/papers/pdfs/
```

然后新建或编辑：

```text
my-idea/papers/manual_papers.json
```

示例：

```json
[
  {
    "title": "Paper title",
    "year": 2025,
    "venue": "Conference or journal",
    "url": "https://example.com",
    "local_pdf": "papers/pdfs/paper.pdf",
    "source": "manual"
  }
]
```

`local_pdf` 支持两种写法：

- 推荐：相对 idea 项目目录，例如 `papers/pdfs/paper.pdf`
- 也可以：绝对路径，例如 `/home/ubuntu/papers/paper.pdf`

如果你暂时没有 PDF，也可以先只写元数据：

```json
[
  {
    "title": "Paper title",
    "abstract": "Short abstract or your note",
    "year": 2025,
    "url": "https://example.com",
    "source": "manual"
  }
]
```

有 `local_pdf` 时，Evidence QA 才能读全文证据。

### 4. 配置 API key

复制本地密钥模板：

```bash
cp my-idea/secrets.local.yaml.example my-idea/secrets.local.yaml
```

编辑 `my-idea/secrets.local.yaml`：

```yaml
model_tiers:
  cheap:
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

ChatGPT Plus 订阅不能直接当 API key。这里需要 OpenAI/GPT-compatible API URL 和 key，可以是第三方中转。

### 5. 检查环境

```bash
python3 -m idea_workbench doctor my-idea
```

重点看：

- 四个模型档位是否 `ready`
- `Paper Search MCP` 是否 available
- `Evidence QA` 是否 available

`doctor` 不会打印 key 内容。

### 6. 先 dry-run 检查流程

```bash
python3 -m idea_workbench run-deep my-idea --dry-run
```

这一步只写 prompt 和运行说明，不调用 LLM，也不联网检索。

输出位置：

```text
my-idea/reports/details/run_deep_dry_run.md
my-idea/traces/dry_run_prompts.json
```

### 7. 生成 evidence pack

```bash
python3 -m idea_workbench run-deep my-idea
```

`run-deep` 会读取：

- `seed.md`
- `papers/manual_papers.json`
- `papers/imported_papers.json`
- 已有 `local_pdf`
- 自动检索得到的论文元数据

`run-deep` 的定位是给后续 `research` 准备证据和初筛结果，不是最终研究方案报告。它会生成：

```text
my-idea/reports/evidence_pack_cn.md
my-idea/reports/final_report_cn.md   # 兼容旧版本；内容同 evidence pack
my-idea/reports/details/novelty_matrix.md
my-idea/reports/details/reviewer_report.md
my-idea/reports/details/evidence_qa.md
```

运行时会在终端输出简洁进度，包括当前阶段、cache hit/miss、文献数量、Evidence QA 状态和最终输出路径。

### 8. 运行主 research proposal workflow

先完成第 7 步 `run-deep`，因为 `research` 会复用它生成的 brief、claims、文献证据、novelty matrix 和 reviewer report。

如果你希望生成过程本身就带审查和修正，而不是先生成一批再最后筛选，运行：

```bash
python3 -m idea_workbench research my-idea
```

默认流程：

```text
机会挖掘 → Builder 生成少量候选 → comprehensive discovery critic → Builder 修正/转向 → Chair 生成最终 proposal
```

它的设计重点：

- 不追求 Transformer / RL 级别范式创新。
- 鼓励高质量机制迁移、failure mode 挖掘、问题重定义、benchmark / evaluation gap 和局部 method 改造。
- Critic 不只是挑错，也必须给 upgrade opportunity、better framing、promising pivot。
- Reviser 必须实质性改造候选 idea，不能只压缩或复述。
- 最终 `research.md` 会按 proposal 结构写清：研究问题、目标问题、方法草图、机制设计、训练/优化信号、实验协议、novelty boundary、stronger baseline、证据基础、待验证假设和失败条件。
- 内部 JSON 字段保持英文；人看的报告默认中文，Transformer、RL、WAM、diffusion model、world model、benchmark、baseline 等术语保留英文。

输出：

```text
my-idea/reports/research.md
my-idea/reports/details/research_rounds.md
my-idea/state/research_workflow.json
my-idea/state/research_stages/*.json
```

可调预算：

```bash
python3 -m idea_workbench research my-idea --ideas 5 --final 3
```

### 9. 可选：运行高质量 idea search

如果你希望工具不只是“打磨原想法”，而是主动生成多个高潜力分支并筛选，运行：

```bash
python3 -m idea_workbench idea-search my-idea
```

默认会做：

```text
构建 literature evidence store → 按阶段检索证据 → 提取瓶颈 → 机制迁移 → 分批生成约20个分支 → 筛到5个 → 最终选3个
```

`idea-search` 现在使用阶段感知 Hybrid RAG：它会把全量论文元数据、已有 novelty/reviewer 结果和可读本地 PDF 片段做成证据库，然后每个阶段只取自己需要的证据。这样不会把完整论文列表反复塞给 LLM，也不会靠简单减少文献数量来避免超时。

输出：

```text
my-idea/reports/idea_search.md
my-idea/state/idea_search.json
my-idea/reports/details/literature_store.md
my-idea/state/literature_store.json
my-idea/state/idea_search_stages/*.json
```

这个命令需要先跑过 `run-deep`，因为它会复用已有的 brief、claims、文献、evidence、novelty matrix 和 reviewer report。

如果你修改了 `papers/*.json` 或本地 PDF，工具会根据论文元数据、摘要和 PDF 文件签名自动判断是否重建证据库。想强制重建证据库：

```bash
python3 -m idea_workbench idea-search my-idea --refresh-evidence-store
```

可调预算：

```bash
python3 -m idea_workbench idea-search my-idea --branches 20 --shortlist 5 --final 3
```

只看 prompt、不调用 LLM：

```bash
python3 -m idea_workbench idea-search my-idea --dry-run
```

## 可选：下载检索结果里的 PDF

`run-deep` 会先检索论文，并写入：

```text
my-idea/papers/api_papers.json
```

如果你想把这些检索结果中能直接找到 PDF 的论文也下载下来，运行：

```bash
python3 -m idea_workbench pdfs my-idea --top 20
```

这里的“自动下载 PDF”只指两类：

- `api_papers.json` 里已经有 `pdf_url`
- arXiv 链接，例如 `https://arxiv.org/abs/xxxx`，自动转为 `https://arxiv.org/pdf/xxxx.pdf`

下载位置：

```text
my-idea/papers/pdfs/
```

并生成：

```text
my-idea/papers/papers_with_pdfs.json
my-idea/reports/details/pdf_downloads.md
```

只想看哪些能解析、不下载：

```bash
python3 -m idea_workbench pdfs my-idea --top 20 --dry-run
```

下载后可以重新跑 Evidence QA：

```bash
python3 -m idea_workbench evidence my-idea
```

注意：`pdfs` 不是导入你手头已有的 PDF。你自己的 PDF 应该按上面的 `manual_papers.json` 方式导入。

## 输出怎么看

第一次看结果时：

- 只跑了 `run-deep`：先看 `reports/evidence_pack_cn.md`。
- 跑完了 `research`：先看 `reports/research.md`，这是主 proposal 报告。
- 额外跑了 `idea-search`：再看 `reports/idea_search.md`，它是多分支探索结果。

常见文件含义：

| 文件 | 用途 |
| --- | --- |
| `reports/research.md` | 主报告：闭环生成后的完整 research proposal |
| `reports/evidence_pack_cn.md` | `run-deep` 生成的证据包和初筛结果 |
| `reports/final_report_cn.md` | 旧版本兼容路径，内容同 evidence pack |
| `reports/idea_search.md` | 多分支 idea search 的最终结果 |
| `reports/details/novelty_matrix.md` | 每个 claim 的查重/重合风险 |
| `reports/details/reviewer_report.md` | 审稿式批判 |
| `reports/details/evidence_qa.md` | PDF 证据问答状态和结果 |
| `reports/details/ingest.md` | 低门槛导入记录 |
| `reports/details/search_log.md` | 文献检索记录 |
| `reports/details/pdf_downloads.md` | PDF 下载记录 |
| `reports/details/research_rounds.md` | 闭环 research 的中间轮次 |

默认只有核心报告放在 `reports/` 根目录；过程报告放在 `reports/details/`，避免一次运行后根目录太乱。

机器可读中间结果在：

```text
my-idea/state/
my-idea/evidence/
my-idea/traces/
```

## 低配或测试模式

没有 API key 时可以跑离线骨架：

```bash
python3 -m idea_workbench run-all my-idea --offline
```

只测试 Evidence QA 报告格式：

```bash
python3 -m idea_workbench evidence my-idea --mock
```

只跳过论文 API，但仍调用 LLM：

```bash
python3 -m idea_workbench run-deep my-idea --offline-search
```

## 常用命令参考

最短主流程：

```bash
python3 -m idea_workbench doctor my-idea
python3 -m idea_workbench run-deep my-idea --dry-run
python3 -m idea_workbench ingest my-idea
python3 -m idea_workbench run-deep my-idea
python3 -m idea_workbench research my-idea
```

需要多分支探索、PDF 下载或单独重跑 evidence 时再用：

```bash
python3 -m idea_workbench idea-search my-idea
python3 -m idea_workbench pdfs my-idea --top 20
python3 -m idea_workbench evidence my-idea
```

需要单独调试时再用：

```bash
python3 -m idea_workbench search my-idea --limit 5
python3 -m idea_workbench matrix my-idea
python3 -m idea_workbench review my-idea
python3 -m idea_workbench report my-idea
```

## 模型档位

默认四个档位：

| 档位 | 默认模型 | 用途 |
| --- | --- | --- |
| `cheap` | `deepseek-chat` | query planning、粗筛、低成本批处理 |
| `standard` | `gpt-5.4` | brief、claim decomposition、实验计划 |
| `strong` | `gpt-5.5`, `high` | novelty matrix、idea refinement |
| `frontier` | `gpt-5.5`, `xhigh` | 最终审稿式批判 |

配置优先级：

1. `secrets.local.yaml`
2. `config.yaml`
3. 环境变量 `GPT_API_BASE_URL` / `GPT_API_KEY`

## 项目目录结构

工具仓库：

```text
Idea-Workbench/
  README.md
  pyproject.toml
  idea_workbench/
  prompts/
  tests/
  third_party/paper-search-mcp/
  THIRD_PARTY_NOTICES.md
```

单个 idea 项目：

```text
my-idea/
  seed.md
  config.yaml
  secrets.local.yaml
  queries.yaml
  papers/
    inbox/
    manual_papers.json
    imported_papers.json
    api_papers.json
    papers_with_pdfs.json
    pdfs/*.pdf
  reports/
    evidence_pack_cn.md
    final_report_cn.md
    idea_search.md
    research.md
    details/
      literature_store.md
      ingest.md
      novelty_matrix.md
      reviewer_report.md
      evidence_qa.md
      search_log.md
      pdf_downloads.md
      research_rounds.md
  state/
    literature_store.json
    idea_search_stages/*.json
    research_workflow.json
    research_stages/*.json
  evidence/
  traces/
```

运行产物、PDF、trace、API key 不应该提交到 Git。

## 第三方代码与版权

本仓库 vendored 了一个必要的第三方检索后端：

| 路径 | 上游 | 许可证 | 用途 |
| --- | --- | --- | --- |
| `third_party/paper-search-mcp` | `https://github.com/openags/paper-search-mcp` | MIT | 多源论文检索 |

已保留上游 `LICENSE`，并在 `THIRD_PARTY_NOTICES.md` 中记录上游地址和 vendored commit。

PaperQA2 没有打包进仓库，通过 PyPI 安装：

```bash
python3 -m pip install paper-qa
```

## 测试

```bash
python3 -B -m unittest discover -s tests -v
```

## 重要边界

- 它不能替代人工读 paper。
- `Novelty Matrix` 是风险排序，不是新颖性证明。
- LLM 可能顺着原始想法继续补全，而不是主动推翻；更强的 falsification-first prompt workflow 还需要后续加强。
- Evidence QA 依赖本地 PDF 和可用的 PaperQA2 / `pqa`。

# Idea Workbench 完整流程手册

这份文档说明如何测试和使用 `idea-workbench`。工具目标是：从一个粗糙科研想法出发，进行 LLM 拆解、文献检索、novelty matrix、PDF/evidence QA、审稿式批判、idea refinement 和最小实验计划。

## 1. 基本概念

一个 idea 项目就是一个普通文件夹，核心文件如下：

| 路径 | 含义 |
| --- | --- |
| `seed.md` | 你的原始想法，越具体越好 |
| `config.yaml` | 非敏感配置：模型档位、检索源、evidence QA 参数 |
| `secrets.local.yaml` | 本地密钥配置，默认不创建，需要从 example 复制，已被 `.gitignore` 忽略 |
| `queries.yaml` | 文献检索 query，可人工编辑 |
| `papers/*.json` | 论文元数据，可来自 API 或人工补充 |
| `papers/pdfs/*.pdf` | `pdfs` 命令下载的本地 PDF |
| `state/*.json` | 机器可读中间状态 |
| `reports/*.md` | 人可读报告 |
| `evidence/*.jsonl` | claim-level evidence QA 结果 |
| `traces/*.jsonl` / `traces/*.prompt.md` | LLM 调用记录和 prompt |

## 2. 初始化项目

在 `idea-workbench` 工具目录中运行：

```bash
cd /home/ubuntu/Awesome-Vibe-Research/local_research_toolbox/idea-workbench
python3 -m idea_workbench init /tmp/my-idea
```

也可以直接写入一行 seed：

```bash
python3 -m idea_workbench init /tmp/my-idea --seed-text "world action model for contact-rich robot manipulation"
```

参数：

| 参数 | 含义 |
| --- | --- |
| `project` | idea 项目目录 |
| `--seed-text` | 直接写入 `seed.md` 的原始想法 |
| `--force` | 覆盖已有 `seed.md`、`config.yaml`、`queries.yaml` 等模板文件，谨慎使用 |

初始化后，编辑：

```bash
/tmp/my-idea/seed.md
```

建议至少写清：

- 想解决的问题
- 直觉上的方法
- 目标任务或实验环境
- 担心已有工作覆盖的部分
- 想重点查新的点

## 3. 配置 API

推荐使用项目本地 `secrets.local.yaml`，不要把 key 写进聊天或公共文件。

```bash
cp /tmp/my-idea/secrets.local.yaml.example /tmp/my-idea/secrets.local.yaml
```

示例：

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

配置优先级：

1. `secrets.local.yaml`
2. `config.yaml`
3. 环境变量 `GPT_API_BASE_URL` / `GPT_API_KEY`

如果所有档位都走同一个中转，也可以用环境变量：

```bash
export GPT_API_BASE_URL="https://your-gpt-relay.example.com/v1"
export GPT_API_KEY="your-relay-key"
```

注意：ChatGPT Plus 订阅不是 API key。这个工具需要 GPT-compatible API URL/key，第三方中转也可以。

## 4. 检查环境

```bash
python3 -m idea_workbench doctor /tmp/my-idea
```

它会检查：

- cheap / standard / strong / frontier 四个模型档位是否 ready
- `paper-search-mcp` 是否可用
- PaperQA2 / `pqa` 是否可用
- key 来源是 `config`、`env` 还是 `missing`

`doctor` 不会打印 key 内容。

## 5. 推荐测试流程

如果你只是想先确认工具能跑，不消耗真实 API：

```bash
python3 -m idea_workbench run-deep /tmp/my-idea --dry-run
```

输出：

```text
reports/run_deep_dry_run.md
traces/dry_run_prompts.json
```

含义：只生成 prompt 和运行说明，不调用 LLM、不检索文献。

如果你想测试 LLM 编排但先不联网检索论文：

```bash
python3 -m idea_workbench run-deep /tmp/my-idea --offline-search
```

这会调用 LLM 做 brief、claims、query planning、review、refine、experiment plan，但跳过文献 API。

如果你想测试 evidence QA 的报告格式：

```bash
python3 -m idea_workbench evidence /tmp/my-idea --mock
```

## 6. 完整深度流程

真实完整流程：

```bash
python3 -m idea_workbench run-deep /tmp/my-idea
```

`run-deep` 做的事情：

1. 读取 `seed.md`
2. `standard` 模型提取 research brief
3. `standard` 模型拆解 claims
4. `cheap` 模型生成文献 query
5. 调用 `paper-search-mcp` 或内置检索 adapter
6. 尝试 PaperQA2 / evidence QA；若还没有本地 PDF，会写明需要先下载
7. `strong` 模型生成 novelty matrix
8. `frontier` 模型做 adversarial review
9. `strong` 模型打磨候选 idea
10. `standard` 模型生成最小实验计划
11. 汇总 `reports/final_report_cn.md`

参数：

| 参数 | 含义 |
| --- | --- |
| `project` | idea 项目目录 |
| `--dry-run` | 只写 prompt，不调用 LLM |
| `--offline-search` | LLM 生成 query 后跳过论文 API |
| `--limit N` | 每个 query、每个 source 最多返回 N 篇论文 |
| `--sources A,B,C` | 指定文献源，例如 `arxiv,openalex,semantic_scholar` |

## 7. 离线/低配流程

如果没有 API key，可以跑旧的规则流程：

```bash
python3 -m idea_workbench run-all /tmp/my-idea --offline
```

这不会调用 LLM，也不会联网。它只用启发式模板生成：

- claims
- queries
- novelty matrix 骨架
- refined ideas
- experiment plan
- final report

参数：

| 参数 | 含义 |
| --- | --- |
| `--offline` | 只生成 query，不调用论文 API |
| `--limit N` | 每个 query、每个 source 最多返回 N 篇论文 |
| `--sources A,B,C` | 指定检索源 |

## 8. 分步骤运行

### 8.1 拆解 idea

```bash
python3 -m idea_workbench decompose /tmp/my-idea
```

输出：

- `state/decomposition.json`
- `reports/decomposition.md`

### 8.2 生成 query 并检索

```bash
python3 -m idea_workbench search /tmp/my-idea
```

只生成 query，不联网：

```bash
python3 -m idea_workbench search /tmp/my-idea --offline
```

参数：

| 参数 | 含义 |
| --- | --- |
| `--offline` | 只生成 query，不调用论文 API |
| `--limit N` | 每个 query、每个 source 最多返回 N 篇论文 |
| `--sources A,B,C` | 指定检索源 |

输出：

- `queries.yaml`
- `papers/api_papers.json`
- `logs/search_errors.json`
- `reports/search_log.md`

### 8.3 单独运行 literature

```bash
python3 -m idea_workbench literature /tmp/my-idea
```

和 `search` 类似，但用于 LLM 流程中已有 `state/queries.json` 的情况。

### 8.4 获取 PDF

```bash
python3 -m idea_workbench pdfs /tmp/my-idea --top 10
```

先只解析 PDF URL、不下载：

```bash
python3 -m idea_workbench pdfs /tmp/my-idea --top 10 --dry-run
```

参数：

| 参数 | 含义 |
| --- | --- |
| `--top N` | 只考察 `papers/*.json` 中排序最靠前的 N 篇论文 |
| `--dry-run` | 只解析 PDF URL 和目标路径，不联网下载 |
| `--force` | 已存在本地 PDF 时也重新下载 |

当前支持：

- 已有 `local_pdf` / `pdf_path`：直接保留
- 已有 `pdf_url`：按该 URL 下载
- arXiv `url` / `doi` / `arxiv_id`：自动转换为 `https://arxiv.org/pdf/<id>.pdf`

输出：

- `papers/papers_with_pdfs.json`
- `papers/pdfs/*.pdf`
- `reports/pdf_downloads.md`

注意：`papers_with_pdfs.json` 会被后续 `evidence` 自动读取。

### 8.5 Evidence QA

```bash
python3 -m idea_workbench evidence /tmp/my-idea
```

mock 模式：

```bash
python3 -m idea_workbench evidence /tmp/my-idea --mock
```

参数：

| 参数 | 含义 |
| --- | --- |
| `--mock` | 不调用 PaperQA2，生成 mock evidence，用于测试格式 |

输入：

- `state/claims.json` 或 `state/decomposition.json`
- `papers/*.json`

论文 JSON 可包含：

```json
{
  "title": "Paper title",
  "year": "2025",
  "url": "https://example.com",
  "pdf_url": "https://example.com/paper.pdf",
  "local_pdf": "/absolute/path/to/paper.pdf",
  "source": "manual"
}
```

当前 adapter 优先使用 `local_pdf`。如果只有 `pdf_url`，会提示需要先下载 PDF。

推荐顺序：

```bash
python3 -m idea_workbench pdfs /tmp/my-idea --top 10
python3 -m idea_workbench evidence /tmp/my-idea
```

输出：

- `reports/evidence_qa.md`
- `evidence/claim_evidence.jsonl`
- `evidence/evidence_status.json`

### 8.6 Novelty matrix

```bash
python3 -m idea_workbench matrix /tmp/my-idea
```

输出：

- `state/novelty_matrix.json`
- `reports/novelty_matrix.md`

### 8.7 Idea refinement

```bash
python3 -m idea_workbench refine /tmp/my-idea
```

输出：

- `state/refined_ideas.json`
- `reports/refined_ideas.md`

### 8.8 实验计划

```bash
python3 -m idea_workbench experiment-plan /tmp/my-idea
```

输出：

- `state/experiment_plan.json`
- `reports/experiment_plan.md`

### 8.9 Frontier review

```bash
python3 -m idea_workbench review /tmp/my-idea
```

只写 prompt，不调用 LLM：

```bash
python3 -m idea_workbench review /tmp/my-idea --dry-run
```

参数：

| 参数 | 含义 |
| --- | --- |
| `--dry-run` | 只生成 prompt，不调用 frontier 模型 |

### 8.10 汇总报告

```bash
python3 -m idea_workbench report /tmp/my-idea
```

输出：

- `reports/final_report_cn.md`

## 9. `config.yaml` 参数说明

### 9.1 基本参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `language` | `zh` | 报告主语言，目前主要面向中文 |
| `max_results_per_query` | `5` | 每个 query、每个 source 的默认返回数量 |
| `domain_keywords` | embodied/world model/robotics 相关词 | 启发式流程和 query 生成使用的领域词 |
| `search_sources` | `arxiv, openalex, semantic_scholar` | 默认文献源 |

### 9.2 `model_tiers`

四个档位：

| 档位 | 默认模型 | 用途 |
| --- | --- | --- |
| `cheap` | `deepseek-chat` | query planning、粗筛、低成本批处理 |
| `standard` | `gpt-5.4` | brief、claim decomposition、实验计划 |
| `strong` | `gpt-5.5` high | novelty matrix、idea refinement |
| `frontier` | `gpt-5.5` xhigh | 最终审稿式批判 |

每个档位支持：

| 字段 | 含义 |
| --- | --- |
| `provider` | 当前支持 `gpt_compatible` |
| `base_url_env` | 环境变量名，默认 `GPT_API_BASE_URL` |
| `api_key_env` | 环境变量名，默认 `GPT_API_KEY` |
| `base_url` | 直接写入配置文件的 API base URL |
| `api_key` | 直接写入配置文件的 API key，建议只写在 `secrets.local.yaml` |
| `model` | 模型名 |
| `reasoning_effort` | 推理强度，如 `low`、`standard`、`high`、`xhigh` |

### 9.3 `evidence_qa`

```yaml
evidence_qa:
  enabled: true
  backend: paperqa2
  max_papers: 8
  max_claims: 8
  require_pdf: true
  mock: false
```

| 字段 | 含义 |
| --- | --- |
| `enabled` | 是否启用 evidence QA |
| `backend` | 当前为 `paperqa2` |
| `max_papers` | 最多选择多少篇带 PDF 的论文 |
| `max_claims` | 最多对多少个 claim 做 QA |
| `require_pdf` | 是否要求 PDF；当前真实 PaperQA2 路径需要 PDF |
| `mock` | 是否默认使用 mock evidence，不调用 PaperQA2 |

## 10. 手动补充论文

如果检索不稳定，可以人工写：

```bash
/tmp/my-idea/papers/manual_papers.json
```

格式：

```json
[
  {
    "title": "Paper title",
    "abstract": "Short abstract or your note",
    "authors": ["Author A", "Author B"],
    "year": 2025,
    "venue": "Conference or journal",
    "url": "https://example.com",
    "pdf_url": "https://example.com/paper.pdf",
    "local_pdf": "/absolute/path/to/paper.pdf",
    "source": "manual"
  }
]
```

然后运行：

```bash
python3 -m idea_workbench evidence /tmp/my-idea
python3 -m idea_workbench matrix /tmp/my-idea
python3 -m idea_workbench report /tmp/my-idea
```

## 11. 常见情况

### 11.1 `doctor` 显示模型 missing

说明没有配置 URL/key。检查：

- `/tmp/my-idea/secrets.local.yaml`
- `/tmp/my-idea/config.yaml`
- shell 环境变量

### 11.2 PaperQA2 unavailable

当前环境未安装 `paperqa` 或 `pqa`。这不会阻塞 `run-deep`，只会生成降级版 `reports/evidence_qa.md`。

### 11.3 只有 `pdf_url` 没有 `local_pdf`

先运行：

```bash
python3 -m idea_workbench pdfs /tmp/my-idea --top 10
```

如果仍无法解析，再人工下载 PDF，并在论文 JSON 里补：

```json
"local_pdf": "/absolute/path/to/paper.pdf"
```

### 11.4 不想花 LLM 额度

使用：

```bash
python3 -m idea_workbench run-deep /tmp/my-idea --dry-run
python3 -m idea_workbench run-all /tmp/my-idea --offline
python3 -m idea_workbench evidence /tmp/my-idea --mock
```

## 12. 最小推荐测试

```bash
cd /home/ubuntu/Awesome-Vibe-Research/local_research_toolbox/idea-workbench

python3 -m idea_workbench init /tmp/test-idea --seed-text "world action model for contact-rich robot manipulation"
python3 -m idea_workbench doctor /tmp/test-idea
python3 -m idea_workbench run-deep /tmp/test-idea --dry-run
python3 -m idea_workbench run-all /tmp/test-idea --offline
python3 -m idea_workbench pdfs /tmp/test-idea --top 10 --dry-run
python3 -m idea_workbench evidence /tmp/test-idea --mock
```

查看：

```bash
/tmp/test-idea/reports/final_report_cn.md
/tmp/test-idea/reports/evidence_qa.md
```

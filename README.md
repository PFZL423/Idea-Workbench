# Idea Workbench

`idea-workbench` 是一个本地科研 idea 打磨工具。它的目标不是自动发论文，而是把一个粗糙想法变成可检查的工作包：拆解 claim、检索文献、读本地 PDF 证据、生成 novelty matrix、给出审稿式批判、打磨候选 idea 和最小实验计划。

它适合这种场景：你已经有一个不成熟想法，也有一些相关论文，想先判断“有没有人做过、哪里最危险、下一步该怎么查和怎么实验”。

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

### 3. 导入你已经知道的相关文献

推荐把 PDF 放到：

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

### 7. 运行深度流程

```bash
python3 -m idea_workbench run-deep my-idea
```

`run-deep` 会读取：

- `seed.md`
- `papers/manual_papers.json`
- 已有 `local_pdf`
- 自动检索得到的论文元数据

它会生成：

```text
my-idea/reports/final_report_cn.md
my-idea/reports/novelty_matrix.md
my-idea/reports/reviewer_report.md
my-idea/reports/evidence_qa.md
```

### 8. 可选：运行高质量 idea search

如果你希望工具不只是“打磨原想法”，而是主动生成多个高潜力分支并筛选，运行：

```bash
python3 -m idea_workbench idea-search my-idea
```

默认会做：

```text
提取瓶颈 → 机制迁移 → 生成约20个分支 → 筛到5个 → 最终选3个
```

输出：

```text
my-idea/reports/idea_search.md
my-idea/state/idea_search.json
```

这个命令需要先跑过 `run-deep`，因为它会复用已有的 brief、claims、文献、evidence、novelty matrix 和 reviewer report。

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
my-idea/reports/pdf_downloads.md
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

优先看这几个文件：

| 文件 | 用途 |
| --- | --- |
| `reports/final_report_cn.md` | 总报告 |
| `reports/novelty_matrix.md` | 每个 claim 的查重/重合风险 |
| `reports/reviewer_report.md` | 审稿式批判 |
| `reports/evidence_qa.md` | PDF 证据问答状态和结果 |
| `reports/search_log.md` | 文献检索记录 |
| `reports/pdf_downloads.md` | PDF 下载记录 |

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

正常使用通常只需要：

```bash
python3 -m idea_workbench doctor my-idea
python3 -m idea_workbench run-deep my-idea --dry-run
python3 -m idea_workbench run-deep my-idea
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
    manual_papers.json
    api_papers.json
    papers_with_pdfs.json
    pdfs/*.pdf
  reports/
  state/
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

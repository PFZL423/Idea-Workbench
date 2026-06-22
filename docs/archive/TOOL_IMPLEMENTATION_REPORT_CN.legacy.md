# Idea Workbench 实现报告

## 工具形式

这是一个本地 Python CLI 工具，使用 Markdown/YAML/JSON 作为文件库。它的代码和每个 idea 项目的数据都在本地目录中，不依赖数据库，也不要求先配置 MCP。

当前实现的是“科研 idea 工作流”，不是全自动科研 agent：

1. 从 `seed.md` 读取一个粗糙想法。
2. 拆成可检索 claims。
3. 为每个 claim 生成精确查重、相邻领域、失败案例三类 query。
4. 可选调用 arXiv、OpenAlex、Semantic Scholar API。
5. 生成 novelty matrix，标出可能重合的已有工作。
6. 生成多个打磨后的 idea 版本。
7. 生成最小实验计划。
8. 汇总为中文报告。

v0.2 新增 LLM 编排层：

1. 使用项目 `secrets.local.yaml`、`config.yaml` 或 `GPT_API_BASE_URL` / `GPT_API_KEY` 环境变量连接 GPT-compatible 中转站。
2. 使用多模型档位：`cheap`、`standard`、`strong`、`frontier`。
3. `standard` 默认 `gpt-5.4`；`strong` 默认 `gpt-5.5` + `reasoning_effort=high`；`frontier` 默认 `gpt-5.5` + `reasoning_effort=xhigh`。
4. 把专业 prompt 放入 `prompts/`，不再只靠死规则提取关键词。
5. 每次 LLM 调用写入 `traces/`，记录模型、档位、prompt hash、输出和错误。
6. 没有 key 时，`run-deep` 不会伪造 LLM 结果；可使用 `--dry-run` 检查 prompt。
7. `cheap` 档可直接配置 DeepSeek 官方 OpenAI-compatible 地址：`https://api.deepseek.com/v1`。

v0.3 新增 evidence QA 层：

1. 新增 `evidence` 命令，输出 `reports/evidence_qa.md` 和 `evidence/claim_evidence.jsonl`。
2. `doctor` 会检测 PaperQA2 / `pqa` 是否可用。
3. `run-deep` 在文献检索后自动尝试 evidence QA。
4. 没有 PaperQA2 或没有本地 PDF 时，生成清晰降级报告，不阻塞主流程。
5. 支持 `--mock` 用于测试 claim-level evidence 报告格式。

## 参考的已有工具思路

- ARIS skills：采用 `research-lit -> idea-creator -> novelty-check -> refine` 的流程骨架。
- AutoDiscovery：借鉴“候选假设、证据、评分、迭代”的结构，但不绑定特定 benchmark。
- SciAgentsDiscovery：借鉴跨概念路径产生 idea 的思路；材料科学图谱没有直接复用。
- paper-search-mcp：作为优先文献检索后端。
- PaperQA2：作为可选 PDF evidence QA 后端。
- RefChecker：保留为后续引用核查组件。

## 当前能力

- 无外部依赖，可以直接 `python -m idea_workbench` 运行。
- 网络不可用时仍可生成 query、报告骨架和实验计划。
- 支持手动补充论文 JSON，再重建 novelty matrix。
- 输出中文报告，英文论文标题/query 原样保留。
- 支持 `doctor` 检查 LLM 和文献后端配置。
- 支持 `run-deep` 运行 LLM-first 深度流程。
- 优先复用本地 `paper-search-mcp` 做多源文献检索；不可用时回退到内置 arXiv/OpenAlex/Semantic Scholar adapter。
- 支持可选 PaperQA2/PDF evidence QA，生成 claim-level evidence 文件。

## 当前限制

- 真实 LLM 流程需要用户在 `secrets.local.yaml`、`config.yaml` 或 shell 环境变量中配置 GPT-compatible URL/key。
- 检索质量依赖公开 API 和 query 质量，无法保证覆盖所有相关论文。
- novelty score 是关键词/摘要重合初筛，不是语义等价判定。
- 实验计划只生成设计，不调用 Genesis、CILD 或其他实验脚本。
- PaperQA2 没有强制安装；当前环境缺失时只生成降级报告。
- 当前 adapter 不自动批量下载 PDF；如果只有 `pdf_url`，需要先通过 `paper-search-mcp` 或人工方式下载到本地并补 `local_pdf`。

## 建议下一步

1. 用 2-3 个你真实但还不成熟的 idea 跑一轮，观察报告是否帮你节省时间。
2. 把人工读到的关键论文加入 `papers/manual_papers.json`，测试 novelty matrix 是否能正确暴露重合风险。
3. 配置 `secrets.local.yaml` 后，用 `doctor` 确认来源为 `config`，再用 `run-deep --offline-search` 先验证 LLM prompt 质量。
4. 安装 PaperQA2 后，用 3-5 篇本地 PDF 验证 `evidence` 命令的真实问答质量。
5. 下一步可实现 PDF 自动下载和 PaperQA2 Python API 直连，减少对 `pqa` CLI 的依赖。
6. 如果后续经常使用，再包装成 Codex skill；如果需要让多个 agent 稳定调用，再做 MCP server。

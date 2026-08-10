# JA Local KB

本目录是跨平台产品实现，和上一级 `40_code` 中的 Windows/Ollama 历史原型分开。

## 当前状态

`0.1.1` 在首个稳定版基础上增加客户知识原生登记与统一过滤，并将
Apple Silicon 安装、插件、STDIO MCP 和重复安装纳入自动化分发门禁。
支持范围必须分开理解：Windows 已验收；macOS 只支持 Apple Silicon
（M 系列芯片），自动化门禁已通过但真实 Mac 端到端验收仍待完成；Intel Mac
不受支持；Claude 真实客户端也尚未验收。Intel 边界来自 LanceDB 当前 macOS
发行包只提供 arm64 轮子：<https://pypi.org/project/lancedb/#files>。
其中 `0.1.1` 客户扩展已通过 Windows 隔离升级、回滚和全量回归，正式日常设备
仍需在 Release 发布后分别完成原地更新验收。

- 已实现显式来源注册、结构化 Chunk、差异 Embedding、SQLite 状态、
  LanceDB 全文/向量/混合检索和失效拒答。
- 已实现且只公开三个本地 STDIO MCP 工具。
- 已在同一来源注册表和三个 MCP 工具内原生兼容客户知识：六种检索模式
  统一支持 `client_ids`，与 `project_ids` 同时提供时使用 AND；Obsidian
  默认识别客户 00—06 并排除归档。
- `recall` 是默认 Agent 模式：8B Embedding 生成最多 80 条可追溯证据，
  直接交给 Claude/Codex 二次筛选、分析和回答，不调用 Reranker。
- `quality` 保留为可选紧凑模式：独立 API Reranker 将同一候选池压缩到
  24-40 条；它不是默认主链路。
- 已构建 Obsidian 来源与状态控制插件：右键显式入库后释放弹窗，
  watcher 后台串行切片和向量化，状态页显示进度，文件树与原生图谱同步标记；
  无变化轮询不再重建侧栏，真实更新会保留滚动与全部折叠状态。
- 已加入默认收起的 Codex、Claude 紧凑连接入口：复制完整提示词后由目标 Agent 检查环境、
  备份并合并 MCP 配置，再以真实工具发现和状态/检索/证据回读验证连接。
- “检索诊断”只展示本次候选证据，不宣称找全；“召回质量报告”只对冻结
  题目和人工标注证据给出正式结论，并在语料、索引、模型或配置变化后标旧。
- 已提供 Windows 与 Apple Silicon macOS 的预检、安装、诊断、更新和回滚脚本；
  macOS 预检会在写入前拒绝 Intel 架构。版本快照不保存 `sources.json`，旧快照
  恢复也不能覆盖设备当前来源。
- 已使用 42 份冻结基准来源、783 个 Chunk 和 12 道冻结交叉项目题完成 4B/8B
  同条件 API 验收。
- 历史冻结基准的主链路为 `Qwen/Qwen3-Embedding-8B + recall Top80`：证据覆盖
  `47/47（100%）`、项目覆盖 `27/27（100%）`、可追溯 `100%`。
- 上述结果对应 42 份来源和索引版本 131；当前来源范围变化后，系统会将
  它标为历史/过期，不能作为当前语料已经完整召回的证明。
- 本次正式单轮复杂题延迟中位数 `3.112 秒`、P95 `35.914 秒`，召回质量
  通过，但 API 长尾速度门槛未通过，必须与质量指标分开呈现。
- 8B Reranker Top40 为 `42/47（89.36%）`，说明紧凑压缩会丢证据；
  仅在上下文预算不足时按需启用。
- 真实 Obsidian、Codex MCP、来源保护与 Windows 相邻版本回滚已经通过；
  Apple Silicon macOS 与 Claude 客户端仍需分别完成真实端到端验证。

## 产品边界

- Obsidian Markdown 是唯一真源。
- 每台设备独立保存来源清单、SQLite 状态与 LanceDB。
- 建库与查询统一使用 API Embedding。
- v1 对 Agent 只公开本地 STDIO MCP。
- Obsidian 插件是来源、状态、操作和连接控制面，不提供聊天回答。
- 首版通过公开 GitHub 稳定 Release 与本地 Agent 辅助部署；正式设备只安装
  已审核 tag/Release，不自动追踪 `main`。

开发与发布只允许使用无真实业务数据的测试夹具。Vault、LanceDB、日志、
API Key、来源注册表和机器配置不得进入 GitHub 或 Release。

## 获取与授权边界

- 代码仓库：<https://github.com/jefferyadventure519-byte/ja-local-kb>
- 稳定版本：<https://github.com/jefferyadventure519-byte/ja-local-kb/releases>
- 本仓库公开是为了简化设备安装和更新，不等于开源；当前没有附加开源许可证。
  复制、修改或二次分发前，应先取得仓库所有者许可。

## 目录入口

- `docs/architecture.md`
- `docs/state-machine.md`
- `docs/mcp-contract.md`
- `contracts/source-registry.schema.json`
- `INSTALL_AGENT.md`
- `UPDATE_AGENT.md`

## 本地开发验证

Python 3.11+、`uv` 和 Node.js 20+ 可用后：

```powershell
$runtimeRoot = 'D:\JA-Local-KB'
$vaultRoot = 'D:\path\to\your-vault'
$env:UV_CACHE_DIR = Join-Path $runtimeRoot 'runtime\uv-cache'
uv sync --all-extras --no-editable
uv run --no-sync ruff check .
pwsh -File scripts\test-windows.ps1 `
  -RuntimeRoot $runtimeRoot `
  -VaultRoot $vaultRoot
npm.cmd --prefix obsidian-plugin run build
uv build
```

Windows 中文路径下使用 `--no-editable`，避免某些 `.pth` 加载器按系统编码读取
UTF-8 路径。修改 Python 源码后需重新执行 `uv sync ... --reinstall-package
ja-local-kb` 再测试。Windows 测试必须通过 `scripts/test-windows.ps1` 运行；
脚本会先同步当前源码，把 pytest 临时目录固定到所选运行根目录的
`tmp\pytest\`，复用 Vault 外的运行时 uv 缓存，并拒绝调用方覆盖
`--basetemp`。不得在任何 Obsidian Vault 内创建 pytest 缓存、临时目录或
隔离目录。

## 官方兼容依据

- OpenAI Embeddings API：
  <https://developers.openai.com/api/reference/resources/embeddings/methods/create>
- Qwen3 Embedding：<https://qwenlm.github.io/blog/qwen3-embedding/>
- SiliconFlow Embeddings API：
  <https://docs.siliconflow.cn/en/api-reference/embeddings/create-embeddings>
- SiliconFlow Rerank API：
  <https://docs.siliconflow.cn/cn/api-reference/rerank/create-rerank>
- Qwen3-Reranker-8B：
  <https://huggingface.co/Qwen/Qwen3-Reranker-8B>
- MCP Python SDK：<https://github.com/modelcontextprotocol/python-sdk>
- Obsidian 插件开发：<https://docs.obsidian.md/Plugins/Getting%20started/Build%20a%20plugin>

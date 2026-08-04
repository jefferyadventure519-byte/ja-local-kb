"""Generate review-first Agent connection instructions for this device."""

# The long Chinese lines below are copied verbatim into Agent chat surfaces.
# ruff: noqa: E501

from __future__ import annotations

import platform
from pathlib import Path

CLIENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Desktop / Claude Code",
    "other": "其他支持 STDIO MCP 的 Agent",
}


def build_connection_guide(
    *,
    client: str,
    python_path: Path,
    settings_path: Path,
) -> dict[str, str]:
    if client not in CLIENT_LABELS:
        raise ValueError(f"Unsupported Agent client: {client}")
    resolved_python = str(python_path.resolve())
    resolved_settings = str(settings_path.resolve())
    config_client = client if client in {"codex", "claude"} else "codex"
    prompt = f"""你正在为我连接 JA Local Knowledge 本地知识库到 {CLIENT_LABELS[client]}。

目标：只增加名为 `ja_local_knowledge` 的本地 STDIO MCP，不修改知识库来源、不重建索引、不上传 Vault，也不覆盖任何现有 MCP。

本机插件已经提供以下候选路径；必须先只读核验，不能根据示例猜路径：
- Python：`{resolved_python}`
- settings：`{resolved_settings}`
- 当前系统：`{platform.system()} {platform.machine()}`

严格按以下流程执行：

1. 先做只读检查
   - 识别当前操作系统、当前 Agent 类型与它实际使用的 MCP 配置文件位置。
   - 检查上面的 Python 和 settings 是否存在、可读；读取 settings 时只核对路径、模型与运行时字段，不输出任何秘密。
   - 用该 Python 执行 `-m ja_local_kb.cli --settings <settings路径> doctor`，展示脱敏结果。
   - 找到现有 Agent 配置并读取；列出已存在的 MCP 名称。不得把整个含敏感信息的配置原样贴回对话。
   - 如果运行时不存在或 doctor 失败：停止写入。只允许从 `https://github.com/jefferyadventure519-byte/ja-local-kb/releases/latest` 获取已审核稳定 Release；禁止从 main、任意分支或未知压缩包安装。先按 Release 内预检脚本展示安装根目录、Vault、依赖、权限、磁盘与全部计划写入，等我再次明确确认。

2. 在任何写入前给我一份精确计划
   - 显示将修改的配置文件绝对路径、备份文件路径、仅新增/更新的 `ja_local_knowledge` 条目，以及是否需要重启 Agent。
   - 明确说明会保留哪些现有 MCP。
   - 等我明确回复确认后再继续；没有确认就停止。

3. 确认后安全连接
   - 先创建带时间戳的原配置备份。
   - 用真实路径执行：`{resolved_python}` `-m` `ja_local_kb.cli` `--settings` `{resolved_settings}` `connection-config` `--client` `{config_client}`。
   - 解析命令返回的 JSON，只合并其中 `ja_local_knowledge` 条目；禁止整体覆盖原配置，禁止改动其他 MCP。
   - 如果目标 Agent 不使用 {config_client} 的配置格式，把同一个 command/args 等价转换为它支持的 STDIO MCP 格式，并先展示差异。
   - API Key 只能保留在系统凭据库/Keychain；不要向我索取、显示、复制或写进 Agent 配置。

4. 写后验证
   - 重新读取配置，证明原 MCP 仍在且 `ja_local_knowledge` 的 command/args 与本机真实路径一致。
   - 再运行一次 doctor。
   - 如需重启或重载 {CLIENT_LABELS[client]}，明确告诉我动作并等待我完成；只看到配置文件不能宣称“已连接”。
   - 在 Agent 真正发现 MCP 后，依次实际调用 `get_knowledge_status`、用一个跨项目问题调用 `search_knowledge`（mode=`recall`），再用返回的一个 evidence_id 调用 `get_source`。
   - 最终分别报告：配置已写入、MCP 已发现、状态可读、检索成功、证据回读成功；任何一步没验证都标为“未验证”，不得笼统说全部完成。

现在只执行第 1 步并给我检查结果与第 2 步计划，不要写入任何文件。"""
    return {
        "client": client,
        "label": CLIENT_LABELS[client],
        "prompt": prompt,
        "python_path": resolved_python,
        "settings_path": resolved_settings,
        "verification_boundary": (
            "配置存在不等于已连接；必须完成 MCP 工具发现与三次真实调用。"
        ),
    }

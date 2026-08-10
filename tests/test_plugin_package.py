from pathlib import Path


def test_white_j_icon_is_shipped_by_desktop_installers() -> None:
    project_root = Path(__file__).resolve().parents[1]
    icon = project_root / "obsidian-plugin" / "j-icon-white.png"
    assert icon.is_file()
    assert icon.stat().st_size > 0

    for relative_path in (
        "scripts/install-windows.ps1",
        "scripts/install-macos.sh",
    ):
        installer = (project_root / relative_path).read_text(encoding="utf-8")
        assert "j-icon-white.png" in installer
        assert "graphHighlightEnabled" in installer
        assert "backgroundBatch" in installer


def test_obsidian_ingestion_is_backgrounded_and_graph_group_is_managed() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "obsidian-plugin" / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    assert 'setButtonText("加入知识库")' in source
    assert "registerSourcesInBackground" in source
    assert (
        "await this.plugin.core.syncSource(result.source.source_id)"
        not in source
    )
    assert 'path:"__JA_LOCAL_KB_MANAGED__"' in source
    assert 'getPluginById?.("graph")?.instance' in source
    assert "view.dataEngine?.setOptions(graphPlugin.options)" in source
    assert "adapter.process(graphPath" in source
    assert "graphHighlightEnabled" in source


def test_obsidian_control_plane_keeps_retrieval_proof_boundaries_visible() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "obsidian-plugin" / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    assert "连接状态需由 Agent 实际验证" in source
    assert "复制连接提示词" in source
    assert "检索诊断" in source
    assert "不能证明已经找全所有相关证据" in source
    assert "召回质量报告" in source
    assert "为什么不能作为当前证明" in source
    assert "测试检索" not in source
    assert "选择同目录文件" not in source
    assert '["search", query, "--mode", "recall"]' in source


def test_obsidian_recognizes_only_active_client_core_documents() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "obsidian-plugin" / "src" / "main.ts"
    ).read_text(encoding="utf-8")

    assert "frontmatter.client_id" in source
    assert "frontmatter.client_short_name" in source
    assert "frontmatter.client" in source
    assert "frontmatter.doc_type" in source
    assert '"client_archive", "80_archive"' in source
    assert '"client_overview"' in source
    assert '"client_source_index"' in source
    assert '"add-client"' in source
    assert "`客户｜${source.project_name || source.client_id}`" in source


def test_obsidian_refresh_and_modal_layout_preserve_user_context() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "obsidian-plugin" / "src" / "main.ts"
    ).read_text(encoding="utf-8")
    styles = (
        project_root / "obsidian-plugin" / "styles.css"
    ).read_text(encoding="utf-8")

    assert "fingerprint === this.lastRenderFingerprint" in source
    assert "captureUiState" in source
    assert "restoreScroll" in source
    assert "expandedProjects" in source
    assert 'cls: "ja-kb-projects"' in source
    assert 'cls: "ja-kb-agents"' in source
    assert source.count("prepareKnowledgeModal(this") == 10
    assert ".modal.ja-kb-modal-shell" in styles
    assert '"Microsoft YaHei UI"' in styles
    assert '"PingFang SC"' in styles

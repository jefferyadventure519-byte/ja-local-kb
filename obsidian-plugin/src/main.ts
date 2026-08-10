import {
  App,
  ItemView,
  Menu,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  setIcon,
  Setting,
  TAbstractFile,
  TFile,
  TFolder,
  WorkspaceLeaf,
} from "obsidian";
import {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  spawn,
} from "child_process";

const VIEW_TYPE = "ja-local-knowledge-view";
const GRAPH_GROUP_MARKER = 'path:"__JA_LOCAL_KB_MANAGED__"';
const GRAPH_GROUP_RGB = 0x426fae;
const CLIENT_PROJECT_ID_PREFIX = "__client__:";
const CLIENT_CORE_DOC_TYPES = new Set([
  "client_overview",
  "client_facts",
  "client_rule_library",
  "client_decision_log",
  "client_experience_library",
  "client_change_log",
  "client_source_index",
]);
const CLIENT_ARCHIVE_SEGMENTS = new Set(["client_archive", "80_archive"]);

interface GraphColorGroup {
  query?: unknown;
  color?: unknown;
}

interface GraphOptions {
  colorGroups?: GraphColorGroup[];
  [key: string]: unknown;
}

interface InternalGraphPlugin {
  options: GraphOptions;
  saveOptions: () => void | Promise<void>;
}

interface InternalGraphEngine {
  setOptions: (options: GraphOptions) => void;
  requestUpdateSearch?: {
    run?: () => void;
  };
  updateSearch?: () => void;
}

interface InternalGraphView {
  dataEngine?: InternalGraphEngine;
  update?: () => void;
}

interface AppWithInternalPlugins {
  internalPlugins?: {
    getPluginById?: (id: string) => {
      instance?: InternalGraphPlugin;
    } | null;
  };
}

interface BackgroundBatch {
  sourceIds: string[];
  relativePaths: Record<string, string>;
  createdAt: string;
}

interface ViewUiStateSettings {
  projectsOpen: boolean;
  expandedProjects: string[];
  agentsOpen: boolean;
  systemOpen: boolean;
}

interface PluginSettings {
  pythonExecutable: string;
  settingsPath: string;
  autoStartWatcher: boolean;
  statusRefreshSeconds: number;
  graphHighlightEnabled: boolean;
  backgroundBatch: BackgroundBatch | null;
  viewUiState: ViewUiStateSettings;
}

const DEFAULT_SETTINGS: PluginSettings = {
  pythonExecutable: "",
  settingsPath: "",
  autoStartWatcher: true,
  statusRefreshSeconds: 10,
  graphHighlightEnabled: true,
  backgroundBatch: null,
  viewUiState: {
    projectsOpen: true,
    expandedProjects: [],
    agentsOpen: false,
    systemOpen: false,
  },
};

interface SourceStatus {
  source_id: string;
  project_id: string;
  project_name: string;
  client_id: string;
  document_role: string;
  relative_path: string;
  enabled: number;
  status: string;
  chunk_count: number;
  error_code: string;
  error_message: string;
  indexed_at: string;
}

interface KnowledgeStatus {
  ready: boolean;
  enabled_source_count: number;
  index_version: number;
  embedding_fingerprint: string;
  vector_dimension: number | null;
  counts: Record<string, number>;
  blocking_sources: Array<{
    source_id: string;
    relative_path: string;
    status: string;
    error_code: string;
  }>;
  sources: SourceStatus[];
}

interface SourceCommandResult {
  action: string;
  source: {
    source_id: string;
    relative_path: string;
  };
}

interface SourceRegistrationInput {
  sourceKind: "project" | "client";
  projectId: string;
  projectName: string;
  clientName: string;
  documentRole: string;
  relativePath: string;
  clientId: string;
}

interface ClientFrontmatterIdentity {
  clientId: string;
  clientName: string;
  documentRole: string;
  eligible: boolean;
  reason: string;
}

function clientFrontmatterIdentity(
  app: App,
  file: TFile,
): ClientFrontmatterIdentity | null {
  const frontmatter = app.metadataCache.getFileCache(file)?.frontmatter ?? {};
  const clientId = String(frontmatter.client_id ?? "").trim();
  const clientShortName = String(frontmatter.client_short_name ?? "").trim();
  const clientName =
    clientShortName || String(frontmatter.client ?? "").trim();
  const documentRole = String(frontmatter.doc_type ?? "").trim();
  if (!clientId || !clientName || !documentRole) {
    return null;
  }
  const pathSegments = file.path
    .split("/")
    .map((segment) => segment.toLocaleLowerCase());
  const archived = pathSegments.some((segment) =>
    CLIENT_ARCHIVE_SEGMENTS.has(segment),
  );
  const coreDocument = CLIENT_CORE_DOC_TYPES.has(documentRole);
  return {
    clientId,
    clientName,
    documentRole,
    eligible: coreDocument && !archived,
    reason: archived
      ? "客户归档默认不纳入知识库"
      : coreDocument
        ? ""
        : "客户目录默认只允许 00—06 文档",
  };
}

interface RegistrationSummary {
  registered: number;
  failed: string[];
}

interface BackgroundProgress {
  total: number;
  completed: number;
  failed: number;
  pending: number;
  activePath: string;
}

interface ConnectionConfig {
  client: "codex" | "claude";
  format: "toml" | "json";
  content: string;
}

type AgentClient = "codex" | "claude" | "other";

interface ConnectionGuide {
  client: AgentClient;
  label: string;
  prompt: string;
  python_path: string;
  settings_path: string;
  verification_boundary: string;
}

interface SearchEvidence {
  evidence_id: string;
  source_id: string;
  project_id: string;
  project_name: string;
  client_id: string;
  document_role: string;
  relative_path: string;
  heading: string;
  source_text: string;
  candidate_rank?: number | null;
  fused_score: number;
}

interface DiagnosticResult {
  query: string;
  requested_mode: string;
  executed_mode: string;
  index_version: number;
  freshness: string;
  evidence_count: number;
  evidence: SearchEvidence[];
  subqueries?: string[];
  routed_projects?: string[];
}

interface DoctorCheck {
  name: string;
  ok: boolean;
  message: string;
}

interface DoctorResult {
  healthy: boolean;
  version: string;
  platform: string;
  python: string;
  checks: DoctorCheck[];
  status: {
    ready: boolean;
    index_version: number;
    counts: Record<string, number>;
    blocking_sources: KnowledgeStatus["blocking_sources"];
  };
}

interface QualitySummary {
  questions?: number;
  full_evidence_questions?: number;
  evidence_groups_hit?: number;
  evidence_groups_total?: number;
  evidence_coverage?: number;
  projects_hit?: number;
  projects_total?: number;
  project_coverage?: number;
  traceable_evidence?: number;
  evidence_returned?: number;
  traceability?: number;
  latency_complex?: {
    samples?: number;
    median_ms?: number;
    p95_ms?: number;
    mean_ms?: number;
  };
}

interface QualityReport {
  status: "current" | "outdated" | "unverified";
  verdict: string;
  scope: string;
  benchmark: {
    label: string;
    created_at: string;
    provenance: string;
    question_count: number;
    mode: string;
    top_k: number | null;
    summary: QualitySummary;
    corpus_snapshot?: Record<string, unknown> | null;
  };
  current: {
    source_count: number;
    chunk_count: number;
    index_version: number;
    embedding_fingerprint: string;
    chunking: Record<string, number>;
    retrieval: Record<string, string | number>;
    corpus_sha256: string;
  };
  comparison: {
    mismatch_reasons: string[];
    unverifiable_reasons: string[];
  };
  limitations: string[];
}

class CoreClient {
  constructor(private readonly plugin: JaLocalKnowledgePlugin) {}

  isConfigured(): boolean {
    return Boolean(
      this.plugin.settings.pythonExecutable &&
        this.plugin.settings.settingsPath,
    );
  }

  async status(): Promise<KnowledgeStatus> {
    return this.run<KnowledgeStatus>(["status"], 30_000);
  }

  async syncAll(): Promise<unknown> {
    return this.run(["sync"], 600_000);
  }

  async syncSource(sourceId: string): Promise<unknown> {
    return this.run(["sync", "--source-id", sourceId], 600_000);
  }

  async rebuild(): Promise<unknown> {
    return this.run(["rebuild", "--confirm"], 1_800_000);
  }

  async doctor(): Promise<DoctorResult> {
    return this.run<DoctorResult>(["doctor"], 60_000);
  }

  async search(query: string): Promise<DiagnosticResult> {
    return this.run<DiagnosticResult>(
      ["search", query, "--mode", "recall"],
      180_000,
    );
  }

  async connectionConfig(
    client: "codex" | "claude",
  ): Promise<ConnectionConfig> {
    return this.run<ConnectionConfig>([
      "connection-config",
      "--client",
      client,
    ]);
  }

  async connectionGuide(client: AgentClient): Promise<ConnectionGuide> {
    return this.run<ConnectionGuide>([
      "connection-guide",
      "--client",
      client,
    ]);
  }

  async qualityReport(): Promise<QualityReport> {
    return this.run<QualityReport>(["quality-report"], 60_000);
  }

  async addSource(input: {
    sourceKind: "project" | "client";
    projectId: string;
    projectName: string;
    clientName: string;
    documentRole: string;
    relativePath: string;
    clientId: string;
  }): Promise<SourceCommandResult> {
    if (input.sourceKind === "client") {
      return this.run<SourceCommandResult>([
        "source",
        "add-client",
        "--client-id",
        input.clientId,
        "--client-name",
        input.clientName,
        "--document-role",
        input.documentRole,
        "--relative-path",
        input.relativePath,
      ]);
    }
    return this.run<SourceCommandResult>([
      "source",
      "add",
      "--project-id",
      input.projectId,
      "--project-name",
      input.projectName,
      "--document-role",
      input.documentRole,
      "--relative-path",
      input.relativePath,
      "--client-id",
      input.clientId,
    ]);
  }

  async removeSource(sourceId: string): Promise<unknown> {
    return this.run(["source", "remove", "--source-id", sourceId]);
  }

  async moveSource(sourceId: string, relativePath: string): Promise<unknown> {
    return this.run([
      "source",
      "move",
      "--source-id",
      sourceId,
      "--relative-path",
      relativePath,
    ]);
  }

  async setSourceEnabled(
    sourceId: string,
    enabled: boolean,
  ): Promise<unknown> {
    return this.run([
      "source",
      enabled ? "enable" : "disable",
      "--source-id",
      sourceId,
    ]);
  }

  startWatcher(): ChildProcess {
    this.assertConfigured();
    const child = spawn(
      this.plugin.settings.pythonExecutable,
      this.baseArgs().concat("watch"),
      {
        windowsHide: true,
        stdio: "ignore",
        env: this.environment(),
      },
    );
    child.on("error", (error) => {
      new Notice(`JA Local Knowledge watcher failed: ${error.message}`);
    });
    return child;
  }

  private async run<T>(
    commandArgs: string[],
    timeoutMs = 120_000,
  ): Promise<T> {
    this.assertConfigured();
    return new Promise<T>((resolve, reject) => {
      const child = spawn(
        this.plugin.settings.pythonExecutable,
        this.baseArgs().concat(commandArgs),
        {
          windowsHide: true,
          env: this.environment(),
        },
      );
      this.collect<T>(child, timeoutMs).then(resolve).catch(reject);
    });
  }

  private collect<T>(
    child: ChildProcessWithoutNullStreams,
    timeoutMs: number,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      let stdout = "";
      let stderr = "";
      const timer = window.setTimeout(() => {
        child.kill();
        reject(new Error("Local knowledge command timed out"));
      }, timeoutMs);
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk: string) => {
        stdout += chunk;
        if (stdout.length > 10_000_000) {
          child.kill();
        }
      });
      child.stderr.on("data", (chunk: string) => {
        stderr += chunk;
      });
      child.on("error", (error) => {
        window.clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        window.clearTimeout(timer);
        try {
          const payload = JSON.parse(stdout) as T & {
            ok?: boolean;
            error?: { message?: string };
          };
          if (code !== 0 || payload.ok === false) {
            reject(
              new Error(
                payload.error?.message ||
                  stderr.trim() ||
                  `Command exited with code ${String(code)}`,
              ),
            );
            return;
          }
          resolve(payload);
        } catch {
          reject(
            new Error(
              stderr.trim() || "Local knowledge command returned invalid JSON",
            ),
          );
        }
      });
    });
  }

  private baseArgs(): string[] {
    return [
      "-m",
      "ja_local_kb.cli",
      "--settings",
      this.plugin.settings.settingsPath,
    ];
  }

  private environment(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      PYTHONUTF8: "1",
    };
  }

  private assertConfigured(): void {
    if (!this.isConfigured()) {
      throw new Error(
        "Configure the Python executable and settings file first.",
      );
    }
  }
}

class KnowledgeView extends ItemView {
  private refreshing = false;
  private lastRenderFingerprint = "";
  private scrollTop = 0;
  private projectsOpen: boolean;
  private agentsOpen: boolean;
  private systemOpen: boolean;
  private readonly expandedProjects: Set<string>;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly plugin: JaLocalKnowledgePlugin,
  ) {
    super(leaf);
    const state = plugin.settings.viewUiState;
    this.projectsOpen = state.projectsOpen;
    this.agentsOpen = state.agentsOpen;
    this.systemOpen = state.systemOpen;
    this.expandedProjects = new Set(state.expandedProjects);
  }

  getViewType(): string {
    return VIEW_TYPE;
  }

  getDisplayText(): string {
    return "JA Local Knowledge";
  }

  getIcon(): string {
    return "database";
  }

  async onOpen(): Promise<void> {
    await this.refresh();
  }

  async refresh(snapshot?: KnowledgeStatus): Promise<void> {
    if (this.refreshing) {
      return;
    }
    this.refreshing = true;
    const container = this.contentEl;
    try {
      if (!this.plugin.core.isConfigured()) {
        const fingerprint = this.fingerprint(null);
        if (fingerprint === this.lastRenderFingerprint) {
          return;
        }
        this.captureUiState(container);
        container.empty();
        container.addClass("ja-kb-view");
        this.renderNotConfigured(container);
        this.lastRenderFingerprint = fingerprint;
        this.restoreScroll(container);
        return;
      }
      const status = snapshot ?? (await this.plugin.loadKnowledgeStatus());
      const fingerprint = this.fingerprint(status);
      if (fingerprint === this.lastRenderFingerprint) {
        return;
      }
      this.captureUiState(container);
      container.empty();
      container.addClass("ja-kb-view");
      this.renderStatus(container, status);
      this.lastRenderFingerprint = fingerprint;
      this.restoreScroll(container);
    } catch (error) {
      if (container.childElementCount === 0) {
        container.addClass("ja-kb-view");
        this.renderError(container, this.message(error));
      }
    } finally {
      this.refreshing = false;
    }
  }

  private fingerprint(status: KnowledgeStatus | null): string {
    return JSON.stringify({
      configured: this.plugin.core.isConfigured(),
      activeFile: this.app.workspace.getActiveFile()?.path ?? "",
      watcherRunning: this.plugin.watcherRunning,
      backgroundBatch: this.plugin.settings.backgroundBatch,
      status,
    });
  }

  private captureUiState(container: HTMLElement): void {
    if (container.childElementCount === 0) {
      return;
    }
    this.scrollTop = container.scrollTop;
    const projects = container.querySelector<HTMLDetailsElement>(
      ".ja-kb-projects",
    );
    if (projects) {
      this.projectsOpen = projects.open;
    }
    const projectDetails = container.querySelectorAll<HTMLDetailsElement>(
      ".ja-kb-project[data-project-key]",
    );
    if (projectDetails.length > 0) {
      this.expandedProjects.clear();
      projectDetails.forEach((details) => {
        const key = details.dataset.projectKey;
        if (details.open && key) {
          this.expandedProjects.add(key);
        }
      });
    }
    const agents = container.querySelector<HTMLDetailsElement>(".ja-kb-agents");
    if (agents) {
      this.agentsOpen = agents.open;
    }
    const system = container.querySelector<HTMLDetailsElement>(".ja-kb-system");
    if (system) {
      this.systemOpen = system.open;
    }
  }

  private restoreScroll(container: HTMLElement): void {
    window.requestAnimationFrame(() => {
      container.scrollTop = this.scrollTop;
    });
  }

  private persistUiState(): void {
    this.plugin.settings.viewUiState = {
      projectsOpen: this.projectsOpen,
      expandedProjects: [...this.expandedProjects],
      agentsOpen: this.agentsOpen,
      systemOpen: this.systemOpen,
    };
    void this.plugin.saveSettings();
  }

  private renderNotConfigured(container: HTMLElement): void {
    container.createEl("h2", { text: "本地知识库" });
    container.createDiv({
      cls: "ja-kb-error",
      text: "尚未配置 Python 运行时与 settings.json。请先打开插件设置。",
    });
  }

  private renderStatus(container: HTMLElement, status: KnowledgeStatus): void {
    const header = container.createDiv({ cls: "ja-kb-header" });
    const heading = header.createDiv();
    heading.createEl("h2", { text: "本地知识库" });
    heading.createDiv({
      cls: "ja-kb-subtitle",
      text: "项目知识检索控制台",
    });
    const refresh = header.createEl("button", {
      cls: "ja-kb-icon-button",
      attr: { "aria-label": "刷新知识库状态" },
    });
    setIcon(refresh, "refresh-cw");
    refresh.addEventListener("click", () => {
      void this.plugin.refreshKnowledgeState();
    });

    this.renderHealth(container, status);
    this.renderBackgroundProgress(container, status);
    this.renderMetrics(container, status);
    this.renderCurrentFile(container, status);
    this.renderProjects(container, status);
    this.renderAgentConnections(container);
    this.renderSystemManagement(container, status);
  }

  private renderError(container: HTMLElement, message: string): void {
    container.createEl("h2", { text: "本地知识库" });
    container.createDiv({ cls: "ja-kb-error", text: message });
    this.button(container, "重试", () => this.plugin.refreshKnowledgeState());
  }

  private renderHealth(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    const card = container.createDiv({
      cls: `ja-kb-health-card${status.ready ? " is-ready" : " is-blocked"}`,
    });
    const copy = card.createDiv();
    const title = copy.createDiv({ cls: "ja-kb-health-title" });
    const dot = title.createSpan({ cls: "ja-kb-health-dot" });
    dot.setAttribute("aria-hidden", "true");
    title.createSpan({
      text: status.ready ? "运行正常" : "需要处理",
    });
    copy.createDiv({
      cls: "ja-kb-health-note",
      text: status.ready
        ? "文档更新后会自动刷新检索结果"
        : "存在尚未完成索引的来源，请展开项目查看",
    });
    const aside = card.createDiv({ cls: "ja-kb-health-aside" });
    aside.createSpan({
      cls: "ja-kb-listening-badge",
      text: this.plugin.watcherRunning ? "实时监听中" : "监听未运行",
    });
    const pending = status.sources.filter((source) =>
      ["not_indexed", "stale"].includes(source.status),
    ).length;
    if (pending > 0) {
      const update = aside.createEl("button", {
        cls: "ja-kb-health-update",
        text: `更新 ${String(pending)} 个文档`,
      });
      update.addEventListener("click", () => {
        void this.syncAll();
      });
    }
  }

  private renderBackgroundProgress(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    const progress = this.plugin.backgroundProgress(status);
    if (!progress || progress.pending === 0) {
      return;
    }
    const card = container.createDiv({ cls: "ja-kb-background-card" });
    const icon = card.createSpan({ cls: "ja-kb-background-icon" });
    setIcon(icon, "loader-circle");
    const copy = card.createDiv({ cls: "ja-kb-background-copy" });
    copy.createDiv({
      cls: "ja-kb-background-title",
      text: `后台处理中 ${progress.completed}/${progress.total}`,
    });
    copy.createDiv({
      cls: "ja-kb-background-note",
      text: progress.activePath
        ? `正在建立：${progress.activePath.split("/").at(-1) ?? progress.activePath}`
        : `${progress.pending} 个文档等待切片与向量化`,
    });
    if (progress.failed > 0) {
      card.createSpan({
        cls: "ja-kb-background-failed",
        text: `${progress.failed} 个失败`,
      });
    }
    const track = card.createDiv({ cls: "ja-kb-background-track" });
    const fill = track.createDiv({ cls: "ja-kb-background-fill" });
    fill.style.width = `${String(
      Math.max(4, (progress.completed / progress.total) * 100),
    )}%`;
  }

  private renderMetrics(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    const fresh = status.sources.filter(
      (source) => source.status === "fresh",
    ).length;
    const errors = status.sources.filter((source) =>
      ["failed", "missing", "identity_conflict"].includes(source.status),
    ).length;
    const waiting = Math.max(0, status.sources.length - fresh - errors);
    const grid = container.createDiv({ cls: "ja-kb-metrics" });
    this.metric(grid, `${fresh}/${status.sources.length}`, "已是最新");
    this.metric(grid, String(waiting), "等待更新");
    this.metric(grid, String(errors), "异常");
  }

  private metric(
    container: HTMLElement,
    value: string,
    label: string,
  ): void {
    const item = container.createDiv({ cls: "ja-kb-metric" });
    item.createDiv({ cls: "ja-kb-metric-value", text: value });
    item.createDiv({ cls: "ja-kb-metric-label", text: label });
  }

  private renderCurrentFile(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    container.createDiv({ cls: "ja-kb-section-label", text: "当前文档" });
    const card = container.createDiv({ cls: "ja-kb-current-card" });
    const active = this.app.workspace.getActiveFile();
    const source = active
      ? status.sources.find((item) => item.relative_path === active.path)
      : undefined;
    const icon = card.createDiv({
      cls: `ja-kb-current-icon${source ? " is-indexed" : ""}`,
    });
    if (source) {
      icon.createEl("img", {
        attr: {
          src: this.plugin.iconResourcePath(),
          alt: "",
          "aria-hidden": "true",
        },
      });
    } else {
      setIcon(icon, "file-text");
    }
    const copy = card.createDiv({ cls: "ja-kb-current-copy" });
    copy.createDiv({
      cls: "ja-kb-current-name",
      text: active?.name ?? "未打开 Markdown",
    });
    copy.createDiv({
      cls: `ja-kb-current-state${source ? " is-indexed" : ""}`,
      text: source ? "已进入知识库" : "尚未进入知识库",
    });
    if (!active) {
      return;
    }
    const action = card.createEl("button", {
      cls: source ? "ja-kb-quiet-button" : "ja-kb-primary-button",
      text: source ? "同步" : "存入",
    });
    action.addEventListener("click", () => {
      if (source) {
        void this.syncSource(source.source_id);
      } else {
        new TrackSourceModal(this.app, this.plugin, active).open();
      }
    });
  }

  private renderProjects(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    const grouped = new Map<
      string,
      { label: string; sources: SourceStatus[] }
    >();
    for (const source of status.sources) {
      const isClient =
        Boolean(source.client_id) &&
        source.project_id.startsWith(CLIENT_PROJECT_ID_PREFIX);
      const key = isClient
        ? `client:${source.client_id}`
        : `project:${source.project_id || source.project_name}`;
      const label = isClient
        ? `客户｜${source.project_name || source.client_id}`
        : source.project_name || source.project_id;
      const group = grouped.get(key) ?? { label, sources: [] };
      group.sources.push(source);
      grouped.set(key, group);
    }
    const section = container.createEl("details", { cls: "ja-kb-projects" });
    section.open = this.projectsOpen;
    section.addEventListener("toggle", () => {
      this.projectsOpen = section.open;
      this.persistUiState();
    });
    const sectionSummary = section.createEl("summary");
    const sectionChevron = sectionSummary.createSpan({
      cls: "ja-kb-section-chevron",
    });
    setIcon(sectionChevron, "chevron-right");
    const sectionCopy = sectionSummary.createSpan({
      cls: "ja-kb-collapsible-copy",
    });
    sectionCopy.createSpan({ text: "已纳入来源" });
    sectionCopy.createEl("small", {
      text: `${String(grouped.size)} 组来源 · ${String(status.sources.length)} 个文档`,
    });
    if (status.sources.length === 0) {
      section.createDiv({
        cls: "ja-kb-empty-card",
        text: "尚未纳入文档。可在左侧文件树右键 Markdown 或文件夹开始。",
      });
      return;
    }
    const list = section.createDiv({ cls: "ja-kb-project-list" });
    for (const [groupKey, group] of [...grouped.entries()].sort(
      ([, left], [, right]) => left.label.localeCompare(right.label, "zh-CN"),
    )) {
      const { label, sources } = group;
      const details = list.createEl("details", { cls: "ja-kb-project" });
      details.dataset.projectKey = groupKey;
      details.open = this.expandedProjects.has(groupKey);
      details.addEventListener("toggle", () => {
        if (details.open) {
          this.expandedProjects.add(groupKey);
        } else {
          this.expandedProjects.delete(groupKey);
        }
        this.persistUiState();
      });
      const summary = details.createEl("summary");
      const chevron = summary.createSpan({ cls: "ja-kb-project-chevron" });
      setIcon(chevron, "chevron-right");
      const copy = summary.createDiv({ cls: "ja-kb-project-copy" });
      copy.createDiv({ cls: "ja-kb-project-name", text: label });
      const fresh = sources.filter(
        (source) => source.status === "fresh",
      ).length;
      copy.createDiv({
        cls: "ja-kb-project-meta",
        text: `${sources.length} 个文档 · ${fresh === sources.length ? "全部最新" : `${fresh} 个最新`}`,
      });
      summary.createSpan({
        cls: "ja-kb-project-count",
        text: String(sources.length),
      });
      const documents = details.createDiv({ cls: "ja-kb-document-list" });
      for (const source of sources.sort((left, right) =>
        left.relative_path.localeCompare(right.relative_path, "zh-CN"),
      )) {
        this.renderDocument(documents, source);
      }
    }
  }

  private renderDocument(
    container: HTMLElement,
    source: SourceStatus,
  ): void {
    const row = container.createDiv({ cls: "ja-kb-document-row" });
    const copy = row.createDiv({ cls: "ja-kb-document-copy" });
    copy.createDiv({
      cls: "ja-kb-document-name",
      text: source.relative_path.split("/").at(-1) ?? source.relative_path,
    });
    copy.createDiv({
      cls: "ja-kb-document-meta",
      text:
        source.status === "fresh"
          ? `${source.chunk_count} 个切片 · 已是最新`
          : source.error_message || source.status,
    });
    const actions = row.createDiv({ cls: "ja-kb-document-actions" });
    this.iconButton(actions, "打开文档", "file-text", () =>
      this.openSource(source),
    );
    this.iconButton(actions, "立即同步", "refresh-cw", () =>
      this.syncSource(source.source_id),
    );
    this.iconButton(actions, "移出知识库", "x", () =>
      this.confirmRemove(source),
    );
  }

  private renderAgentConnections(container: HTMLElement): void {
    const section = container.createEl("details", { cls: "ja-kb-agents" });
    section.open = this.agentsOpen;
    section.addEventListener("toggle", () => {
      this.agentsOpen = section.open;
      this.persistUiState();
    });
    const summary = section.createEl("summary");
    const icon = summary.createSpan({ cls: "ja-kb-collapsible-icon" });
    setIcon(icon, "plug-zap");
    const summaryCopy = summary.createSpan({ cls: "ja-kb-collapsible-copy" });
    summaryCopy.createSpan({ text: "Agent 连接（首次设置）" });
    summaryCopy.createEl("small", {
      text: "连接状态需由 Agent 实际验证",
    });
    summary.createSpan({ cls: "ja-kb-agent-badge", text: "按需使用" });
    const chevron = summary.createSpan({ cls: "ja-kb-section-chevron" });
    setIcon(chevron, "chevron-right");
    const rows = section.createDiv({ cls: "ja-kb-agent-list" });
    this.renderAgentRow(
      rows,
      "codex",
      "Codex",
      "terminal-square",
      "自动检测环境并验证 MCP",
    );
    this.renderAgentRow(
      rows,
      "claude",
      "Claude",
      "sparkles",
      "兼容 Desktop 与 Claude Code",
    );
    const other = rows.createEl("button", {
      cls: "ja-kb-agent-other",
    });
    const otherIcon = other.createSpan();
    setIcon(otherIcon, "plug-zap");
    const otherCopy = other.createSpan();
    otherCopy.createSpan({ text: "其他 STDIO MCP Agent" });
    otherCopy.createEl("small", { text: "打开通用连接提示词" });
    const arrow = other.createSpan();
    setIcon(arrow, "arrow-right");
    other.addEventListener("click", () => {
      void this.previewConnectionGuide("other");
    });
  }

  private renderAgentRow(
    container: HTMLElement,
    client: AgentClient,
    label: string,
    iconName: string,
    description: string,
  ): void {
    const row = container.createDiv({ cls: "ja-kb-agent-row" });
    const icon = row.createDiv({ cls: "ja-kb-agent-icon" });
    setIcon(icon, iconName);
    const copy = row.createDiv({ cls: "ja-kb-agent-copy" });
    copy.createDiv({ cls: "ja-kb-agent-name", text: label });
    copy.createDiv({
      cls: "ja-kb-agent-state",
      text: description,
    });
    const actions = row.createDiv({ cls: "ja-kb-agent-actions" });
    const copyButton = actions.createEl("button", {
      cls: "ja-kb-agent-copy-button",
      text: "复制提示词",
    });
    copyButton.addEventListener("click", () => {
      void this.copyConnectionGuide(client, copyButton);
    });
    const preview = actions.createEl("button", {
      cls: "ja-kb-agent-preview",
      text: "预览",
    });
    preview.addEventListener("click", () => {
      void this.previewConnectionGuide(client);
    });
  }

  private renderSystemManagement(
    container: HTMLElement,
    status: KnowledgeStatus,
  ): void {
    const details = container.createEl("details", {
      cls: "ja-kb-system",
    });
    details.open = this.systemOpen;
    details.addEventListener("toggle", () => {
      this.systemOpen = details.open;
      this.persistUiState();
    });
    const summary = details.createEl("summary");
    const icon = summary.createSpan();
    setIcon(icon, "settings-2");
    const summaryCopy = summary.createSpan({ cls: "ja-kb-system-summary-copy" });
    summaryCopy.createSpan({ text: "系统管理" });
    summaryCopy.createEl("small", { text: "诊断、质量报告与维护" });
    const chevron = summary.createSpan({
      cls: "ja-kb-system-chevron",
    });
    setIcon(chevron, "chevron-down");
    const tools = details.createDiv({ cls: "ja-kb-system-tools" });
    this.systemTool(
      tools,
      "检索诊断",
      "查看当前候选证据，不代表完整召回",
      "scan-search",
      () => this.openDiagnostic(),
    );
    this.systemTool(
      tools,
      "召回质量报告",
      "冻结题目基准，并自动判断是否过期",
      "badge-check",
      () => this.showQualityReport(),
    );
    this.systemTool(
      tools,
      "系统检查",
      "凭据、来源与检索就绪状态",
      "stethoscope",
      () =>
      this.showDoctor(),
    );
    this.systemTool(
      tools,
      "系统信息",
      "运行时、索引与模型摘要",
      "info",
      () => this.showSystemInfo(status),
    );
    const danger = details.createDiv({ cls: "ja-kb-danger-zone" });
    const dangerCopy = danger.createDiv();
    dangerCopy.createDiv({ cls: "ja-kb-danger-title", text: "完整重建" });
    dangerCopy.createDiv({
      cls: "ja-kb-danger-note",
      text: "重新调用 Embedding API，仅在索引损坏或配置迁移时使用。",
    });
    const rebuild = danger.createEl("button", { text: "开始重建" });
    rebuild.addEventListener("click", () => this.confirmRebuild());
  }

  private button(
    container: HTMLElement,
    label: string,
    action: () => void | Promise<void>,
  ): HTMLButtonElement {
    const button = container.createEl("button", { text: label });
    button.addEventListener("click", () => {
      void action();
    });
    return button;
  }

  private iconButton(
    container: HTMLElement,
    label: string,
    icon: string,
    action: () => void | Promise<void>,
  ): HTMLButtonElement {
    const button = container.createEl("button", {
      cls: "ja-kb-row-icon-button",
      attr: { "aria-label": label },
    });
    setIcon(button, icon);
    button.addEventListener("click", () => {
      void action();
    });
    return button;
  }

  private systemTool(
    container: HTMLElement,
    label: string,
    description: string,
    icon: string,
    action: () => void | Promise<void>,
  ): void {
    const button = container.createEl("button", {
      cls: "ja-kb-system-tool",
    });
    const graphic = button.createSpan();
    setIcon(graphic, icon);
    const copy = button.createSpan({ cls: "ja-kb-system-tool-copy" });
    copy.createSpan({ text: label });
    copy.createEl("small", { text: description });
    const arrow = button.createSpan({ cls: "ja-kb-system-tool-arrow" });
    setIcon(arrow, "chevron-right");
    button.addEventListener("click", () => {
      void action();
    });
  }

  private async syncAll(): Promise<void> {
    await this.withNotice("正在同步全部来源…", async () => {
      await this.plugin.core.syncAll();
    });
  }

  private async syncSource(sourceId: string): Promise<void> {
    await this.withNotice("正在同步文档…", async () => {
      await this.plugin.core.syncSource(sourceId);
    });
  }

  private async toggleSource(source: SourceStatus): Promise<void> {
    await this.withNotice("正在更新来源状态…", async () => {
      await this.plugin.core.setSourceEnabled(
        source.source_id,
        !Boolean(source.enabled),
      );
    });
  }

  private confirmRemove(source: SourceStatus): void {
    new ConfirmModal(
      this.app,
      `仅从本地知识库移出“${source.relative_path}”，不会删除原文档。`,
      "确认移出",
      async () => {
        await this.withNotice("正在移出来源…", async () => {
          await this.plugin.core.removeSource(source.source_id);
        });
      },
    ).open();
  }

  private confirmRebuild(): void {
    new ConfirmModal(
      this.app,
      "完整重建会重新调用 Embedding API，并在成功后原子替换派生索引；旧索引保留为本机备份，Vault 不会被修改。",
      "确认完整重建",
      async () => {
        await this.withNotice("正在完整重建，请勿退出 Obsidian…", async () => {
          await this.plugin.core.rebuild();
        });
      },
    ).open();
  }

  private openDiagnostic(): void {
    new DiagnosticQueryModal(this.app, this.plugin).open();
  }

  private async openCandidates(): Promise<void> {
    const active = this.app.workspace.getActiveFile();
    if (!active?.parent) {
      new Notice("请先打开目标文件夹中的一篇 Markdown");
      return;
    }
    try {
      const status = await this.plugin.loadKnowledgeStatus();
      new CandidateModal(
        this.app,
        this.plugin,
        `选择来源：${active.parent.path}`,
        this.plugin.directMarkdownFiles(active.parent),
        new Set(status.sources.map((source) => source.relative_path)),
      ).open();
    } catch (error) {
      new Notice(this.message(error));
    }
  }

  private async showDoctor(): Promise<void> {
    try {
      const result = await this.plugin.core.doctor();
      new DoctorModal(this.app, result).open();
    } catch (error) {
      new Notice(this.message(error));
    }
  }

  private async showQualityReport(): Promise<void> {
    try {
      const result = await this.plugin.core.qualityReport();
      new QualityReportModal(this.app, result).open();
    } catch (error) {
      new Notice(this.message(error));
    }
  }

  private showSystemInfo(status: KnowledgeStatus): void {
    new SystemInfoModal(this.app, this.plugin, status).open();
  }

  private async previewConnectionGuide(client: AgentClient): Promise<void> {
    try {
      const guide = await this.plugin.core.connectionGuide(client);
      new ConnectionGuideModal(this.app, guide).open();
    } catch (error) {
      new Notice(this.message(error));
    }
  }

  private async copyConnectionGuide(
    client: AgentClient,
    button: HTMLButtonElement,
  ): Promise<void> {
    const original = button.textContent ?? "复制连接提示词";
    button.disabled = true;
    button.setText("正在生成…");
    try {
      const guide = await this.plugin.core.connectionGuide(client);
      await navigator.clipboard.writeText(guide.prompt);
      button.setText("已复制");
      new Notice(`${guide.label} 连接提示词已复制`);
      window.setTimeout(() => button.setText(original), 1600);
    } catch (error) {
      button.setText(original);
      new Notice(this.message(error));
    } finally {
      button.disabled = false;
    }
  }

  private async withNotice(
    pending: string,
    operation: () => Promise<void>,
  ): Promise<void> {
    new Notice(pending);
    try {
      await operation();
      new Notice("操作完成");
      await this.plugin.refreshKnowledgeState();
    } catch (error) {
      new Notice(this.message(error));
      await this.plugin.refreshKnowledgeState();
    }
  }

  private async openSource(source: SourceStatus): Promise<void> {
    const file = this.app.vault.getAbstractFileByPath(source.relative_path);
    if (!(file instanceof TFile)) {
      new Notice("原文档不存在");
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
}

function prepareKnowledgeModal(
  modal: Modal,
  ...contentClasses: string[]
): HTMLElement {
  modal.modalEl.addClass("ja-kb-modal-shell");
  modal.contentEl.addClass("ja-kb-modal", ...contentClasses);
  return modal.contentEl;
}

class TrackSourceModal extends Modal {
  private sourceKind: "project" | "client" = "project";
  private projectId: string;
  private projectName: string;
  private clientName = "";
  private documentRole: string;
  private clientId: string;
  private clientEligible = true;
  private clientReason = "";
  private submitting = false;
  private submitButton: HTMLButtonElement | null = null;

  constructor(
    app: App,
    private readonly plugin: JaLocalKnowledgePlugin,
    private readonly file: TFile,
  ) {
    super(app);
    const frontmatter =
      app.metadataCache.getFileCache(file)?.frontmatter ?? {};
    const clientIdentity = clientFrontmatterIdentity(app, file);
    if (clientIdentity) {
      this.sourceKind = "client";
      this.projectId = "";
      this.projectName = "";
      this.clientId = clientIdentity.clientId;
      this.clientName = clientIdentity.clientName;
      this.documentRole = clientIdentity.documentRole;
      this.clientEligible = clientIdentity.eligible;
      this.clientReason = clientIdentity.reason;
    } else {
      this.projectId = String(frontmatter.project_id ?? "");
      this.projectName = String(
        frontmatter.project ?? file.parent?.name ?? file.basename,
      );
      this.documentRole = String(frontmatter.doc_type ?? file.basename);
      this.clientId = String(frontmatter.client_id ?? "");
    }
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-source-modal");
    content.createEl("h2", {
      text: this.sourceKind === "client" ? "纳入客户文档" : "纳入当前文档",
    });
    content.createEl("p", {
      text: this.file.path,
    });
    content.createEl("p", {
      text: "提交后弹窗会关闭，切片与向量化将在后台完成。",
    });
    const grid = content.createDiv({ cls: "ja-kb-modal-grid" });
    if (this.sourceKind === "client") {
      new Setting(grid).setName("客户").setDesc(`客户｜${this.clientName}`);
      new Setting(grid).setName("Client ID").setDesc(this.clientId);
      new Setting(grid).setName("文档角色").setDesc(this.documentRole);
      if (!this.clientEligible) {
        grid.createDiv({ cls: "ja-kb-empty", text: this.clientReason });
      }
    } else {
      this.textSetting(grid, "Project ID", this.projectId, (value) => {
        this.projectId = value;
      });
      this.textSetting(grid, "项目名称", this.projectName, (value) => {
        this.projectName = value;
      });
      this.textSetting(grid, "文档角色", this.documentRole, (value) => {
        this.documentRole = value;
      });
      this.textSetting(grid, "Client ID（可空）", this.clientId, (value) => {
        this.clientId = value;
      });
    }
    new Setting(grid).addButton((button) =>
      {
        button
          .setCta()
          .setButtonText("加入知识库")
          .setDisabled(!this.clientEligible)
          .onClick(() => {
            void this.submit();
          });
        this.submitButton = button.buttonEl;
      },
    );
  }

  onClose(): void {
    this.contentEl.empty();
  }

  private textSetting(
    container: HTMLElement,
    name: string,
    value: string,
    change: (value: string) => void,
  ): void {
    new Setting(container)
      .setName(name)
      .addText((text) => text.setValue(value).onChange(change));
  }

  private async submit(): Promise<void> {
    if (this.submitting) {
      return;
    }
    if (this.sourceKind === "client" && !this.clientEligible) {
      new Notice(this.clientReason);
      return;
    }
    if (
      this.sourceKind === "project" &&
      (!this.projectId || !this.projectName || !this.documentRole)
    ) {
      new Notice("Project ID、项目名称和文档角色不能为空");
      return;
    }
    this.submitting = true;
    if (this.submitButton) {
      this.submitButton.disabled = true;
      this.submitButton.setText("正在登记…");
    }
    try {
      const summary = await this.plugin.registerSourcesInBackground([
        {
          sourceKind: this.sourceKind,
          projectId: this.projectId,
          projectName: this.projectName,
          clientName: this.clientName,
          documentRole: this.documentRole,
          relativePath: this.file.path,
          clientId: this.clientId,
        },
      ]);
      if (summary.registered > 0) {
        this.close();
        new Notice("已加入知识库，正在后台建立索引");
        void this.plugin.refreshKnowledgeState();
      }
      if (summary.failed.length > 0) {
        new Notice(summary.failed.join("\n"));
      }
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    } finally {
      this.submitting = false;
      if (this.submitButton) {
        this.submitButton.disabled = false;
        this.submitButton.setText("加入知识库");
      }
    }
  }
}

interface CandidateSource {
  file: TFile;
  sourceKind: "project" | "client";
  projectId: string;
  projectName: string;
  clientName: string;
  documentRole: string;
  clientId: string;
  selected: boolean;
  valid: boolean;
  tracked: boolean;
  reason: string;
}

class CandidateModal extends Modal {
  private readonly candidates: CandidateSource[];
  private submitting = false;
  private submitButton: HTMLButtonElement | null = null;

  constructor(
    app: App,
    private readonly plugin: JaLocalKnowledgePlugin,
    private readonly title: string,
    files: TFile[],
    trackedPaths: Set<string>,
  ) {
    super(app);
    this.candidates = files
      .map((file) => {
        const frontmatter =
          app.metadataCache.getFileCache(file)?.frontmatter ?? {};
        const clientIdentity = clientFrontmatterIdentity(app, file);
        if (clientIdentity) {
          return {
            file,
            sourceKind: "client" as const,
            projectId: "",
            projectName: "",
            clientName: clientIdentity.clientName,
            documentRole: clientIdentity.documentRole,
            clientId: clientIdentity.clientId,
            selected: false,
            valid: clientIdentity.eligible,
            tracked: trackedPaths.has(file.path),
            reason: clientIdentity.reason,
          };
        }
        const projectId = String(frontmatter.project_id ?? "").trim();
        const projectName = String(
          frontmatter.project ?? file.parent?.name ?? file.basename,
        ).trim();
        const documentRole = String(
          frontmatter.doc_type ?? file.basename,
        ).trim();
        return {
          file,
          sourceKind: "project" as const,
          projectId,
          projectName,
          clientName: "",
          documentRole,
          clientId: String(frontmatter.client_id ?? "").trim(),
          selected: false,
          valid: Boolean(projectId && projectName && documentRole),
          tracked: trackedPaths.has(file.path),
          reason: projectId && projectName && documentRole
            ? ""
            : "缺少 project_id，无法安全纳入",
        };
      })
      .sort((left, right) =>
        left.file.path.localeCompare(right.file.path, "zh-CN"),
      );
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-candidate-modal");
    content.createEl("h2", {
      text: this.title,
    });
    content.createEl("p", {
      text:
        "仅勾选的 Markdown 会进入知识库；子文件夹不会被递归读取。" +
        "提交后将在后台完成切片与向量化。",
    });
    if (this.candidates.length === 0) {
      content.createDiv({
        cls: "ja-kb-empty",
        text: "当前范围没有 Markdown。",
      });
      return;
    }
    for (const candidate of this.candidates) {
      new Setting(content)
        .setName(candidate.file.name)
        .setDesc(
          candidate.tracked
            ? "已在知识库"
            : candidate.valid
            ? candidate.sourceKind === "client"
              ? `客户｜${candidate.clientName} / ${candidate.documentRole}`
              : `${candidate.projectId} / ${candidate.documentRole}`
            : candidate.reason,
        )
        .addToggle((toggle) => {
          toggle.setValue(candidate.tracked);
          toggle.setDisabled(candidate.tracked || !candidate.valid);
          toggle.onChange((value) => {
            candidate.selected = value;
          });
        });
    }
    new Setting(content).addButton((button) =>
      {
        button
          .setCta()
          .setButtonText("加入知识库")
          .onClick(() => {
            void this.submit();
          });
        this.submitButton = button.buttonEl;
      },
    );
  }

  private async submit(): Promise<void> {
    if (this.submitting) {
      return;
    }
    const selected = this.candidates.filter(
      (candidate) => candidate.selected && !candidate.tracked,
    );
    if (selected.length === 0) {
      new Notice("请至少选择一个有效文件");
      return;
    }
    this.submitting = true;
    if (this.submitButton) {
      this.submitButton.disabled = true;
      this.submitButton.setText("正在登记…");
    }
    try {
      const summary = await this.plugin.registerSourcesInBackground(
        selected.map((candidate) => ({
          sourceKind: candidate.sourceKind,
          projectId: candidate.projectId,
          projectName: candidate.projectName,
          clientName: candidate.clientName,
          documentRole: candidate.documentRole,
          relativePath: candidate.file.path,
          clientId: candidate.clientId,
        })),
      );
      if (summary.registered > 0) {
        this.close();
        new Notice(
          `已加入后台处理：${String(summary.registered)} 个文件`,
        );
        void this.plugin.refreshKnowledgeState();
      }
      if (summary.failed.length > 0) {
        new Notice(
          `${String(summary.failed.length)} 个文件登记失败：\n` +
            summary.failed.join("\n"),
        );
      }
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    } finally {
      this.submitting = false;
      if (this.submitButton) {
        this.submitButton.disabled = false;
        this.submitButton.setText("加入知识库");
      }
    }
  }
}

class ConfirmModal extends Modal {
  constructor(
    app: App,
    private readonly prompt: string,
    private readonly confirmLabel: string,
    private readonly confirm: () => Promise<void>,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-confirm-modal");
    content.createEl("p", { text: this.prompt });
    const setting = new Setting(content);
    setting.addButton((button) =>
      button.setButtonText("取消").onClick(() => this.close()),
    );
    setting.addButton((button) =>
      button
        .setWarning()
        .setButtonText(this.confirmLabel)
        .onClick(() => {
          this.close();
          void this.confirm();
        }),
    );
  }
}

class OutputModal extends Modal {
  constructor(
    app: App,
    private readonly title: string,
    private readonly output: string,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-output-modal");
    content.createEl("h2", { text: this.title });
    content.createEl("pre", { cls: "ja-kb-output", text: this.output });
    new Setting(content).addButton((button) =>
      button.setButtonText("复制").onClick(async () => {
        await navigator.clipboard.writeText(this.output);
        new Notice("已复制");
      }),
    );
  }
}

class ConnectionGuideModal extends Modal {
  constructor(
    app: App,
    private readonly guide: ConnectionGuide,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-guide-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({ cls: "ja-kb-modal-icon" });
    setIcon(icon, "plug-zap");
    const copy = header.createDiv();
    copy.createEl("h2", { text: `${this.guide.label} 连接提示词` });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: "复制给对应 Agent；它会先检查并等待确认，不会直接改配置。",
    });
    content.createDiv({
      cls: "ja-kb-boundary-callout",
      text: this.guide.verification_boundary,
    });
    content.createEl("pre", {
      cls: "ja-kb-guide-prompt",
      text: this.guide.prompt,
    });
    const actions = content.createDiv({ cls: "ja-kb-modal-actions" });
    const close = actions.createEl("button", { text: "关闭" });
    close.addEventListener("click", () => this.close());
    const button = actions.createEl("button", {
      cls: "mod-cta",
      text: "复制完整提示词",
    });
    button.addEventListener("click", () => {
      void navigator.clipboard.writeText(this.guide.prompt).then(() => {
        button.setText("已复制");
        new Notice("完整连接提示词已复制");
      });
    });
  }
}

class DiagnosticQueryModal extends Modal {
  private query = "";
  private submitButton: HTMLButtonElement | null = null;

  constructor(
    app: App,
    private readonly plugin: JaLocalKnowledgePlugin,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-diagnostic-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({ cls: "ja-kb-modal-icon" });
    setIcon(icon, "scan-search");
    const copy = header.createDiv();
    copy.createEl("h2", { text: "检索诊断" });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: "使用与 Agent 相同的 recall 高召回路径，查看候选证据。",
    });
    content.createDiv({
      cls: "ja-kb-boundary-callout is-warning",
      text: "这里能证明系统找到了哪些证据，不能证明已经找全所有相关证据。完整性只能通过冻结题目与人工标注的召回质量报告验证。",
    });
    const field = content.createDiv({ cls: "ja-kb-query-field" });
    field.createEl("label", { text: "输入实际问题" });
    const textarea = field.createEl("textarea", {
      attr: {
        rows: "5",
        placeholder: "例如：综合多个项目，找出相关决策、例外与未完成事项……",
      },
    });
    textarea.addEventListener("input", () => {
      this.query = textarea.value.trim();
      if (this.submitButton) {
        this.submitButton.disabled = !this.query;
      }
    });
    const actions = content.createDiv({ cls: "ja-kb-modal-actions" });
    const cancel = actions.createEl("button", { text: "取消" });
    cancel.addEventListener("click", () => this.close());
    this.submitButton = actions.createEl("button", {
      cls: "mod-cta",
      text: "查看候选证据",
    });
    this.submitButton.disabled = true;
    this.submitButton.addEventListener("click", () => {
      void this.submit();
    });
  }

  private async submit(): Promise<void> {
    if (!this.query) {
      new Notice("请输入诊断问题");
      return;
    }
    if (this.submitButton) {
      this.submitButton.disabled = true;
      this.submitButton.setText("正在检索…");
    }
    try {
      const startedAt = performance.now();
      const result = await this.plugin.core.search(this.query);
      const elapsedMs = performance.now() - startedAt;
      this.close();
      new DiagnosticResultsModal(this.app, result, elapsedMs).open();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
      if (this.submitButton) {
        this.submitButton.disabled = false;
        this.submitButton.setText("查看候选证据");
      }
    }
  }
}

class DiagnosticResultsModal extends Modal {
  constructor(
    app: App,
    private readonly result: DiagnosticResult,
    private readonly elapsedMs: number,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-results-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({ cls: "ja-kb-modal-icon" });
    setIcon(icon, "search-check");
    const copy = header.createDiv();
    copy.createEl("h2", { text: "候选证据" });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: this.result.query,
    });
    content.createDiv({
      cls: "ja-kb-boundary-callout is-warning",
      text: "诊断结果只展示本次找到的候选证据，不代表所有相关证据已经被完整召回。",
    });
    const metrics = content.createDiv({ cls: "ja-kb-result-metrics" });
    this.metric(metrics, String(this.result.evidence_count), "候选证据");
    this.metric(metrics, `${(this.elapsedMs / 1000).toFixed(2)}s`, "本次用时");
    this.metric(metrics, String(this.result.index_version), "索引版本");
    const list = content.createDiv({ cls: "ja-kb-evidence-list" });
    for (const evidence of this.result.evidence.slice(0, 8)) {
      this.renderEvidence(list, evidence);
    }
    const remaining = this.result.evidence.slice(8);
    if (remaining.length > 0) {
      const details = content.createEl("details", {
        cls: "ja-kb-evidence-more",
      });
      details.createEl("summary", {
        text: `展开其余 ${String(remaining.length)} 条候选证据`,
      });
      const moreList = details.createDiv({ cls: "ja-kb-evidence-list" });
      for (const evidence of remaining) {
        this.renderEvidence(moreList, evidence);
      }
    }
    this.technicalDetails(content, this.result, "复制完整证据包");
  }

  private metric(container: HTMLElement, value: string, label: string): void {
    const metric = container.createDiv({ cls: "ja-kb-result-metric" });
    metric.createEl("strong", { text: value });
    metric.createEl("small", { text: label });
  }

  private renderEvidence(
    container: HTMLElement,
    evidence: SearchEvidence,
  ): void {
    const card = container.createDiv({ cls: "ja-kb-evidence-card" });
    const rank = evidence.candidate_rank ?? container.childElementCount;
    const top = card.createDiv({ cls: "ja-kb-evidence-top" });
    top.createSpan({ cls: "ja-kb-evidence-rank", text: `#${String(rank)}` });
    const heading = top.createDiv();
    heading.createDiv({
      cls: "ja-kb-evidence-heading",
      text: evidence.heading || "无标题切片",
    });
    heading.createDiv({
      cls: "ja-kb-evidence-project",
      text: evidence.project_name || evidence.project_id,
    });
    card.createDiv({
      cls: "ja-kb-evidence-text",
      text:
        evidence.source_text.length > 520
          ? `${evidence.source_text.slice(0, 520)}…`
          : evidence.source_text,
    });
    const footer = card.createDiv({ cls: "ja-kb-evidence-footer" });
    footer.createSpan({ text: evidence.relative_path });
    footer.createEl("code", { text: evidence.evidence_id });
  }

  private technicalDetails(
    container: HTMLElement,
    payload: unknown,
    copyLabel: string,
  ): void {
    const details = container.createEl("details", {
      cls: "ja-kb-technical-details",
    });
    details.createEl("summary", { text: "技术详情" });
    const output = JSON.stringify(payload, null, 2);
    details.createEl("pre", { text: output });
    const button = details.createEl("button", { text: copyLabel });
    button.addEventListener("click", () => {
      void navigator.clipboard.writeText(output).then(() => {
        button.setText("已复制");
      });
    });
  }
}

class DoctorModal extends Modal {
  constructor(
    app: App,
    private readonly result: DoctorResult,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-doctor-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({
      cls: `ja-kb-modal-icon${this.result.healthy ? "" : " is-warning"}`,
    });
    setIcon(icon, this.result.healthy ? "badge-check" : "triangle-alert");
    const copy = header.createDiv();
    copy.createEl("h2", {
      text: this.result.healthy ? "系统检查通过" : "系统需要处理",
    });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: `版本 ${this.result.version} · Python ${this.result.python}`,
    });
    const checks = content.createDiv({ cls: "ja-kb-check-list" });
    for (const check of this.result.checks) {
      const row = checks.createDiv({
        cls: `ja-kb-check-row${check.ok ? " is-ok" : " is-failed"}`,
      });
      const checkIcon = row.createSpan();
      setIcon(checkIcon, check.ok ? "circle-check" : "circle-x");
      const rowCopy = row.createDiv();
      rowCopy.createEl("strong", { text: this.checkName(check.name) });
      rowCopy.createEl("small", { text: check.message });
    }
    technicalDetails(content, this.result, "复制脱敏诊断");
  }

  private checkName(name: string): string {
    const labels: Record<string, string> = {
      embedding_credential: "Embedding 凭据",
      reranker_credential: "Reranker 凭据",
      vault_root: "Obsidian Vault",
      registered_sources: "已登记来源",
      retrieval_ready: "检索就绪",
    };
    return labels[name] ?? name;
  }
}

class QualityReportModal extends Modal {
  constructor(
    app: App,
    private readonly report: QualityReport,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-quality-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({
      cls: `ja-kb-modal-icon is-${this.report.status}`,
    });
    setIcon(icon, this.report.status === "current" ? "badge-check" : "history");
    const copy = header.createDiv();
    copy.createEl("h2", { text: "召回质量报告" });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: this.report.benchmark.label,
    });
    content.createDiv({
      cls: `ja-kb-quality-verdict is-${this.report.status}`,
      text: this.report.verdict,
    });
    const summary = this.report.benchmark.summary;
    const metrics = content.createDiv({ cls: "ja-kb-quality-metrics" });
    this.metric(
      metrics,
      `${String(summary.evidence_groups_hit ?? 0)}/${String(summary.evidence_groups_total ?? 0)}`,
      "预期证据",
      summary.evidence_coverage,
    );
    this.metric(
      metrics,
      `${String(summary.projects_hit ?? 0)}/${String(summary.projects_total ?? 0)}`,
      "项目覆盖",
      summary.project_coverage,
    );
    this.metric(
      metrics,
      `${Math.round((summary.traceability ?? 0) * 100)}%`,
      "可追溯",
      summary.traceability,
    );
    const compare = content.createDiv({ cls: "ja-kb-quality-compare" });
    compare.createEl("h3", { text: "报告适用性" });
    this.compareRow(
      compare,
      "纳入文档",
      String(
        (this.report.benchmark.corpus_snapshot?.source_count as number | undefined) ??
          "未知",
      ),
      String(this.report.current.source_count),
    );
    this.compareRow(
      compare,
      "索引版本",
      String(
        (this.report.benchmark.corpus_snapshot?.index_version as number | undefined) ??
          "未知",
      ),
      String(this.report.current.index_version),
    );
    const reasons = [
      ...this.report.comparison.mismatch_reasons,
      ...this.report.comparison.unverifiable_reasons,
    ];
    if (reasons.length > 0) {
      const list = content.createDiv({ cls: "ja-kb-quality-reasons" });
      list.createEl("strong", { text: "为什么不能作为当前证明" });
      const ul = list.createEl("ul");
      for (const reason of reasons) {
        ul.createEl("li", { text: reason });
      }
    }
    const latency = summary.latency_complex;
    if (latency) {
      content.createDiv({
        cls: "ja-kb-quality-latency",
        text:
          `历史单轮复杂题：中位 ${(Number(latency.median_ms ?? 0) / 1000).toFixed(3)}s` +
          ` · P95 ${(Number(latency.p95_ms ?? 0) / 1000).toFixed(3)}s。速度与召回质量分开判断。`,
      });
    }
    const limits = content.createEl("details", {
      cls: "ja-kb-technical-details",
    });
    limits.createEl("summary", { text: "这份报告能证明什么" });
    limits.createEl("p", { text: this.report.scope });
    const ul = limits.createEl("ul");
    for (const limitation of this.report.limitations) {
      ul.createEl("li", { text: limitation });
    }
    technicalDetails(content, this.report, "复制完整报告");
  }

  private metric(
    container: HTMLElement,
    value: string,
    label: string,
    ratio?: number,
  ): void {
    const item = container.createDiv({ cls: "ja-kb-quality-metric" });
    item.createEl("strong", { text: value });
    item.createEl("small", { text: label });
    const track = item.createDiv({ cls: "ja-kb-quality-track" });
    const fill = track.createDiv({ cls: "ja-kb-quality-fill" });
    fill.style.width = `${String(Math.max(0, Math.min(100, (ratio ?? 0) * 100)))}%`;
  }

  private compareRow(
    container: HTMLElement,
    label: string,
    benchmark: string,
    current: string,
  ): void {
    const row = container.createDiv({ cls: "ja-kb-compare-row" });
    row.createSpan({ text: label });
    row.createSpan({ text: `基准 ${benchmark}` });
    row.createSpan({ text: `当前 ${current}` });
  }
}

class SystemInfoModal extends Modal {
  constructor(
    app: App,
    private readonly plugin: JaLocalKnowledgePlugin,
    private readonly status: KnowledgeStatus,
  ) {
    super(app);
  }

  onOpen(): void {
    const content = prepareKnowledgeModal(this, "ja-kb-info-modal");
    const header = content.createDiv({ cls: "ja-kb-modal-header" });
    const icon = header.createDiv({ cls: "ja-kb-modal-icon" });
    setIcon(icon, "info");
    const copy = header.createDiv();
    copy.createEl("h2", { text: "系统信息" });
    copy.createDiv({
      cls: "ja-kb-modal-subtitle",
      text: "本机运行时与派生索引摘要，不包含 API Key。",
    });
    const list = content.createDiv({ cls: "ja-kb-info-list" });
    this.row(list, "Python", this.plugin.settings.pythonExecutable);
    this.row(list, "配置", this.plugin.settings.settingsPath);
    this.row(list, "索引版本", String(this.status.index_version));
    this.row(list, "向量维度", String(this.status.vector_dimension ?? "未建立"));
    this.row(list, "已登记来源", String(this.status.sources.length));
    this.row(list, "监听状态", this.plugin.watcherRunning ? "运行中" : "未运行");
    this.row(list, "Embedding", this.status.embedding_fingerprint);
  }

  private row(container: HTMLElement, label: string, value: string): void {
    const row = container.createDiv({ cls: "ja-kb-info-row" });
    row.createSpan({ text: label });
    row.createEl("code", { text: value });
  }
}

function technicalDetails(
  container: HTMLElement,
  payload: unknown,
  copyLabel: string,
): void {
  const details = container.createEl("details", {
    cls: "ja-kb-technical-details",
  });
  details.createEl("summary", { text: "技术详情" });
  const output = JSON.stringify(payload, null, 2);
  details.createEl("pre", { text: output });
  const button = details.createEl("button", { text: copyLabel });
  button.addEventListener("click", () => {
    void navigator.clipboard.writeText(output).then(() => {
      button.setText("已复制");
    });
  });
}

class SettingsTab extends PluginSettingTab {
  constructor(
    app: App,
    private readonly plugin: JaLocalKnowledgePlugin,
  ) {
    super(app, plugin);
  }

  display(): void {
    const container = this.containerEl;
    container.empty();
    container.createEl("h2", { text: "JA Local Knowledge" });
    new Setting(container)
      .setName("Python 可执行文件")
      .setDesc("指向本机 ja-local-kb 虚拟环境中的 Python。")
      .addText((text) =>
        text
          .setPlaceholder("/path/to/.venv/bin/python")
          .setValue(this.plugin.settings.pythonExecutable)
          .onChange(async (value) => {
            this.plugin.settings.pythonExecutable = value.trim();
            await this.plugin.saveSettings();
          }),
      );
    new Setting(container)
      .setName("settings.json")
      .setDesc("本机运行时配置；不得包含 API Key。")
      .addText((text) =>
        text
          .setPlaceholder("/path/to/settings.json")
          .setValue(this.plugin.settings.settingsPath)
          .onChange(async (value) => {
            this.plugin.settings.settingsPath = value.trim();
            await this.plugin.saveSettings();
          }),
      );
    new Setting(container)
      .setName("自动启动增量监听")
      .setDesc("Obsidian 打开期间监听已登记 Markdown 的变更与重命名。")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoStartWatcher)
          .onChange(async (value) => {
            this.plugin.settings.autoStartWatcher = value;
            await this.plugin.saveSettings();
            if (value) {
              this.plugin.startWatcher();
            } else {
              this.plugin.stopWatcher();
            }
          }),
      );
    new Setting(container)
      .setName("关系图谱标记")
      .setDesc("使用原生颜色组将已登记文档标为蓝色；不修改 Markdown。")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.graphHighlightEnabled)
          .onChange(async (value) => {
            this.plugin.settings.graphHighlightEnabled = value;
            await this.plugin.saveSettings();
            await this.plugin.syncGraphHighlight();
          }),
      );
    new Setting(container)
      .setName("状态刷新间隔")
      .setDesc("状态页打开时的刷新秒数，最少 5 秒。")
      .addText((text) =>
        text
          .setValue(String(this.plugin.settings.statusRefreshSeconds))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            if (Number.isFinite(parsed)) {
              this.plugin.settings.statusRefreshSeconds = Math.max(5, parsed);
              await this.plugin.saveSettings();
              this.plugin.installRefreshInterval();
            }
          }),
      );
    container.createEl("p", {
      text: "API Key 仅通过系统凭据库配置，插件不会读取或保存。",
    });
  }
}

export default class JaLocalKnowledgePlugin extends Plugin {
  settings: PluginSettings = DEFAULT_SETTINGS;
  core = new CoreClient(this);
  private watcher: ChildProcess | null = null;
  private watcherRestartTimeoutId: number | null = null;
  private watcherRestartAttempts = 0;
  private watcherStopRequested = false;
  private unloaded = false;
  private refreshIntervalId: number | null = null;
  private knowledgeStatus: KnowledgeStatus | null = null;
  private statusRequest: Promise<KnowledgeStatus> | null = null;
  private readonly sourcesByPath = new Map<string, SourceStatus>();
  private readonly pendingLifecycleSourceIds = new Set<string>();
  private lifecycleQueue: Promise<void> = Promise.resolve();
  private treeObserver: MutationObserver | null = null;
  private treeDecorationFrame: number | null = null;
  private graphSyncQueue: Promise<void> = Promise.resolve();
  private graphSyncError = "";

  get watcherRunning(): boolean {
    return this.watcher !== null && this.watcher.exitCode === null;
  }

  async onload(): Promise<void> {
    this.unloaded = false;
    await this.loadSettings();
    this.registerView(
      VIEW_TYPE,
      (leaf) => new KnowledgeView(leaf, this),
    );
    this.addRibbonIcon("database", "JA Local Knowledge", () => {
      void this.activateView();
    });
    this.addCommand({
      id: "open-local-knowledge",
      name: "打开本地知识库状态页",
      callback: () => {
        void this.activateView();
      },
    });
    this.addCommand({
      id: "track-current-file",
      name: "纳入当前文档",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) {
          return false;
        }
        if (!checking) {
          new TrackSourceModal(this.app, this, file).open();
        }
        return true;
      },
    });
    this.addCommand({
      id: "sync-current-file",
      name: "同步当前文档",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) {
          return false;
        }
        if (!checking) {
          void this.syncCurrentFile(file);
        }
        return true;
      },
    });
    this.registerEvent(
      this.app.workspace.on("file-menu", (menu, file) => {
        this.addFileMenuItems(menu, file);
      }),
    );
    this.registerEvent(
      this.app.workspace.on("files-menu", (menu, files) => {
        this.addFilesMenuItems(menu, files);
      }),
    );
    this.registerEvent(
      this.app.workspace.on("file-open", () => {
        void this.refreshViewsFromSnapshot();
      }),
    );
    this.registerEvent(
      this.app.workspace.on("layout-change", () => {
        void this.syncGraphHighlight();
      }),
    );
    this.registerEvent(
      this.app.vault.on("delete", (file) => {
        this.handleDeletedPath(file.path);
      }),
    );
    this.registerEvent(
      this.app.vault.on("rename", (file, oldPath) => {
        this.handleRenamedPath(file, oldPath);
      }),
    );
    this.addSettingTab(new SettingsTab(this.app, this));
    this.installRefreshInterval();
    if (this.settings.autoStartWatcher && this.core.isConfigured()) {
      this.startWatcher();
    }
    this.app.workspace.onLayoutReady(() => {
      this.startTreeObserver();
      void this.refreshKnowledgeState();
    });
  }

  onunload(): void {
    this.unloaded = true;
    this.stopWatcher();
    this.stopTreeObserver();
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
  }

  async loadSettings(): Promise<void> {
    const loaded = (await this.loadData()) as Partial<PluginSettings> | null;
    this.settings = {
      ...DEFAULT_SETTINGS,
      ...(loaded ?? {}),
      viewUiState: {
        ...DEFAULT_SETTINGS.viewUiState,
        ...(loaded?.viewUiState ?? {}),
      },
    };
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  startWatcher(): void {
    if (this.watcherRunning || !this.core.isConfigured()) {
      return;
    }
    this.clearWatcherRestart();
    this.watcherStopRequested = false;
    try {
      const watcher = this.core.startWatcher();
      const startedAt = Date.now();
      this.watcher = watcher;
      watcher.once("close", (code, signal) => {
        if (this.watcher === watcher) {
          this.watcher = null;
        }
        void this.refreshKnowledgeState();
        if (
          this.watcherStopRequested ||
          this.unloaded ||
          !this.settings.autoStartWatcher
        ) {
          return;
        }
        if (Date.now() - startedAt >= 60_000) {
          this.watcherRestartAttempts = 0;
        }
        const delay = this.watcherRestartDelay();
        const reason =
          signal !== null
            ? `signal ${signal}`
            : `code ${String(code)}`;
        new Notice(
          `JA Local Knowledge watcher stopped (${reason}); ` +
            `retrying in ${String(delay / 1000)}s.`,
        );
        this.watcherRestartTimeoutId = window.setTimeout(() => {
          this.watcherRestartTimeoutId = null;
          this.startWatcher();
        }, delay);
      });
      void this.refreshKnowledgeState();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    }
  }

  stopWatcher(): void {
    this.watcherStopRequested = true;
    this.clearWatcherRestart();
    if (this.watcherRunning) {
      this.watcher?.kill();
    }
    this.watcher = null;
    this.watcherRestartAttempts = 0;
    void this.refreshKnowledgeState();
  }

  private watcherRestartDelay(): number {
    const delays = [1_000, 3_000, 10_000, 30_000, 60_000];
    const index = Math.min(
      this.watcherRestartAttempts,
      delays.length - 1,
    );
    this.watcherRestartAttempts += 1;
    return delays[index] ?? 60_000;
  }

  private clearWatcherRestart(): void {
    if (this.watcherRestartTimeoutId !== null) {
      window.clearTimeout(this.watcherRestartTimeoutId);
      this.watcherRestartTimeoutId = null;
    }
  }

  installRefreshInterval(): void {
    if (this.refreshIntervalId !== null) {
      window.clearInterval(this.refreshIntervalId);
    }
    this.refreshIntervalId = window.setInterval(() => {
      void this.refreshKnowledgeState();
    }, this.settings.statusRefreshSeconds * 1000);
    this.registerInterval(this.refreshIntervalId);
  }

  async activateView(): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? undefined;
      if (!leaf) {
        new Notice("无法创建本地知识库状态页");
        return;
      }
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
  }

  async loadKnowledgeStatus(): Promise<KnowledgeStatus> {
    if (this.statusRequest) {
      return this.statusRequest;
    }
    this.statusRequest = this.core
      .status()
      .then((status) => {
        this.acceptStatus(status);
        return status;
      })
      .finally(() => {
        this.statusRequest = null;
      });
    return this.statusRequest;
  }

  async refreshKnowledgeState(): Promise<void> {
    if (!this.core.isConfigured()) {
      await this.refreshViewsFromSnapshot();
      return;
    }
    try {
      const status = await this.loadKnowledgeStatus();
      await this.refreshViews(status);
    } catch (error) {
      if (this.knowledgeStatus) {
        await this.refreshViews(this.knowledgeStatus);
      }
      throw error;
    }
  }

  async refreshView(): Promise<void> {
    await this.refreshKnowledgeState();
  }

  directMarkdownFiles(folder: TFolder): TFile[] {
    return folder.children
      .filter(
        (child): child is TFile =>
          child instanceof TFile && child.extension.toLowerCase() === "md",
      )
      .sort((left, right) => left.path.localeCompare(right.path, "zh-CN"));
  }

  async registerSourcesInBackground(
    inputs: SourceRegistrationInput[],
  ): Promise<RegistrationSummary> {
    const registered: SourceCommandResult[] = [];
    const failed: string[] = [];
    for (const input of inputs) {
      try {
        registered.push(await this.core.addSource(input));
      } catch (error) {
        failed.push(
          `${input.relativePath}: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    }
    if (registered.length === 0) {
      return { registered: 0, failed };
    }
    const existing = this.settings.backgroundBatch;
    const sourceIds = new Set(existing?.sourceIds ?? []);
    const relativePaths = { ...(existing?.relativePaths ?? {}) };
    for (const result of registered) {
      sourceIds.add(result.source.source_id);
      relativePaths[result.source.source_id] = result.source.relative_path;
    }
    this.settings.backgroundBatch = {
      sourceIds: [...sourceIds],
      relativePaths,
      createdAt: existing?.createdAt ?? new Date().toISOString(),
    };
    await this.saveSettings();
    if (this.settings.autoStartWatcher && !this.watcherRunning) {
      this.startWatcher();
    }
    return { registered: registered.length, failed };
  }

  backgroundProgress(
    status: KnowledgeStatus,
  ): BackgroundProgress | null {
    const statusById = new Map(
      status.sources.map((source) => [source.source_id, source]),
    );
    const batch = this.settings.backgroundBatch;
    if (batch && batch.sourceIds.length > 0) {
      const sources = batch.sourceIds
        .map((sourceId) => statusById.get(sourceId))
        .filter((source): source is SourceStatus => Boolean(source));
      if (sources.length === 0) {
        return null;
      }
      const completed = sources.filter(
        (source) => source.status === "fresh",
      ).length;
      const failed = sources.filter((source) =>
        ["failed", "missing", "identity_conflict"].includes(source.status),
      ).length;
      const active = sources.find((source) => source.status === "syncing");
      return {
        total: sources.length,
        completed,
        failed,
        pending: Math.max(0, sources.length - completed - failed),
        activePath: active?.relative_path ?? "",
      };
    }
    const pending = status.sources.filter((source) =>
      ["not_indexed", "stale", "syncing"].includes(source.status),
    );
    if (pending.length === 0) {
      return null;
    }
    const active = pending.find((source) => source.status === "syncing");
    return {
      total: pending.length,
      completed: 0,
      failed: 0,
      pending: pending.length,
      activePath: active?.relative_path ?? "",
    };
  }

  async syncGraphHighlight(): Promise<void> {
    this.graphSyncQueue = this.graphSyncQueue
      .then(async () => {
        await this.updateGraphColorGroup();
        this.graphSyncError = "";
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        if (message !== this.graphSyncError) {
          this.graphSyncError = message;
          new Notice(`知识库图谱标记更新失败：${message}`);
        }
      });
    await this.graphSyncQueue;
  }

  iconResourcePath(): string {
    const pluginDirectory =
      this.manifest.dir ?? `.obsidian/plugins/${this.manifest.id}`;
    return this.app.vault.adapter.getResourcePath(
      `${pluginDirectory}/j-icon-white.png`,
    );
  }

  private acceptStatus(status: KnowledgeStatus): void {
    this.knowledgeStatus = status;
    this.sourcesByPath.clear();
    for (const source of status.sources) {
      this.sourcesByPath.set(source.relative_path, source);
    }
    this.reconcileBackgroundBatch(status);
    this.scheduleTreeDecoration();
    void this.syncGraphHighlight();
  }

  private reconcileBackgroundBatch(status: KnowledgeStatus): void {
    const batch = this.settings.backgroundBatch;
    if (!batch) {
      return;
    }
    const statusById = new Map(
      status.sources.map((source) => [source.source_id, source]),
    );
    const sourceIds = batch.sourceIds.filter((sourceId) =>
      statusById.has(sourceId),
    );
    if (sourceIds.length === 0) {
      this.settings.backgroundBatch = null;
      void this.saveSettings();
      return;
    }
    const completed = sourceIds.every(
      (sourceId) => statusById.get(sourceId)?.status === "fresh",
    );
    if (completed) {
      this.settings.backgroundBatch = null;
      void this.saveSettings();
      new Notice(`后台索引完成：${String(sourceIds.length)} 个文件`);
      return;
    }
    if (sourceIds.length !== batch.sourceIds.length) {
      const relativePaths: Record<string, string> = {};
      for (const sourceId of sourceIds) {
        relativePaths[sourceId] =
          batch.relativePaths[sourceId] ??
          statusById.get(sourceId)?.relative_path ??
          "";
      }
      this.settings.backgroundBatch = {
        ...batch,
        sourceIds,
        relativePaths,
      };
      void this.saveSettings();
    }
  }

  private async updateGraphColorGroup(): Promise<void> {
    const graphPlugin = (
      this.app as unknown as AppWithInternalPlugins
    ).internalPlugins?.getPluginById?.("graph")?.instance;
    const query = this.graphGroupQuery();
    if (graphPlugin) {
      graphPlugin.options.colorGroups = this.managedGraphColorGroups(
        graphPlugin.options.colorGroups,
        query,
      );
      await graphPlugin.saveOptions();
      for (const leaf of this.app.workspace.getLeavesOfType("graph")) {
        const view = leaf.view as unknown as InternalGraphView;
        view.dataEngine?.setOptions(graphPlugin.options);
        if (view.dataEngine?.requestUpdateSearch?.run) {
          view.dataEngine.requestUpdateSearch.run();
        } else {
          view.dataEngine?.updateSearch?.();
        }
        view.update?.();
      }
      return;
    }
    const graphPath = `${this.app.vault.configDir}/graph.json`;
    if (!(await this.app.vault.adapter.exists(graphPath))) {
      return;
    }
    await this.app.vault.adapter.process(graphPath, (content) => {
      const parsed = JSON.parse(content) as GraphOptions;
      parsed.colorGroups = this.managedGraphColorGroups(
        parsed.colorGroups,
        query,
      );
      return `${JSON.stringify(parsed, null, 2)}\n`;
    });
  }

  private managedGraphColorGroups(
    existing: GraphColorGroup[] | undefined,
    query: string,
  ): GraphColorGroup[] {
    const unmanaged = (Array.isArray(existing) ? existing : []).filter(
      (group) =>
        typeof group.query !== "string" ||
        !group.query.includes(GRAPH_GROUP_MARKER),
    );
    if (!this.settings.graphHighlightEnabled) {
      return unmanaged;
    }
    return [
      {
        query,
        color: {
          a: 1,
          rgb: GRAPH_GROUP_RGB,
        },
      },
      ...unmanaged,
    ];
  }

  private graphGroupQuery(): string {
    const paths = [...this.sourcesByPath.keys()].sort((left, right) =>
      left.localeCompare(right, "zh-CN"),
    );
    if (paths.length === 0) {
      return GRAPH_GROUP_MARKER;
    }
    const alternatives = paths.map((path) =>
      path.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"),
    );
    return `${GRAPH_GROUP_MARKER} OR path:/^(?:${alternatives.join("|")})$/`;
  }

  private async refreshViews(
    status?: KnowledgeStatus,
  ): Promise<void> {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    await Promise.all(
      leaves.map(async (leaf) => {
        const view = leaf.view;
        if (view instanceof KnowledgeView) {
          await view.refresh(status);
        }
      }),
    );
  }

  private async refreshViewsFromSnapshot(): Promise<void> {
    await this.refreshViews(this.knowledgeStatus ?? undefined);
  }

  private addFileMenuItems(menu: Menu, file: TAbstractFile): void {
    if (file instanceof TFolder) {
      menu.addItem((item) =>
        item
          .setTitle("选择直属 Markdown 存入知识库")
          .setIcon("folder-search-2")
          .onClick(() => {
            this.openCandidateFiles(
              `选择直属 Markdown：${file.path}`,
              this.directMarkdownFiles(file),
            );
          }),
      );
      return;
    }
    if (!(file instanceof TFile) || file.extension.toLowerCase() !== "md") {
      return;
    }
    if (!this.knowledgeStatus) {
      menu.addItem((item) =>
        item
          .setTitle("知识库状态加载中")
          .setIcon("loader-circle")
          .setDisabled(true),
      );
      return;
    }
    const source = this.sourcesByPath.get(file.path);
    if (!source) {
      menu.addItem((item) =>
        item
          .setTitle("存入知识库")
          .setIcon("database-zap")
          .onClick(() => {
            new TrackSourceModal(this.app, this, file).open();
          }),
      );
      return;
    }
    menu.addItem((item) =>
      item
        .setTitle("立即同步知识库")
        .setIcon("refresh-cw")
        .onClick(() => {
          void this.syncTrackedSource(source);
        }),
    );
    menu.addItem((item) =>
      item
        .setTitle("移出知识库")
        .setIcon("database-x")
        .onClick(() => {
          this.confirmRemoveSource(source);
        }),
    );
  }

  private addFilesMenuItems(menu: Menu, files: TAbstractFile[]): void {
    const markdownFiles = files.filter(
      (file): file is TFile =>
        file instanceof TFile && file.extension.toLowerCase() === "md",
    );
    if (markdownFiles.length === 0) {
      return;
    }
    menu.addItem((item) =>
      item
        .setTitle("选择所选 Markdown 存入知识库")
        .setIcon("database-zap")
        .setDisabled(!this.knowledgeStatus)
        .onClick(() => {
          this.openCandidateFiles(
            `存入所选 Markdown（${markdownFiles.length}）`,
            markdownFiles,
          );
        }),
    );
  }

  private openCandidateFiles(title: string, files: TFile[]): void {
    if (!this.knowledgeStatus) {
      new Notice("知识库状态尚未加载，请稍后重试");
      return;
    }
    new CandidateModal(
      this.app,
      this,
      title,
      files,
      new Set(this.sourcesByPath.keys()),
    ).open();
  }

  private async syncTrackedSource(source: SourceStatus): Promise<void> {
    try {
      new Notice("正在同步文档…");
      await this.core.syncSource(source.source_id);
      new Notice("文档已同步");
      await this.refreshKnowledgeState();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    }
  }

  private confirmRemoveSource(source: SourceStatus): void {
    new ConfirmModal(
      this.app,
      `仅从本地知识库永久移除“${source.relative_path}”的来源记录、切片与向量；不会删除原文档。`,
      "确认移出",
      async () => {
        await this.core.removeSource(source.source_id);
        new Notice("已从知识库永久移除，原文档保持不变");
        await this.refreshKnowledgeState();
      },
    ).open();
  }

  private handleDeletedPath(deletedPath: string): void {
    const sources = this.sourcesForPath(deletedPath);
    if (sources.length === 0) {
      return;
    }
    for (const source of sources) {
      this.pendingLifecycleSourceIds.add(source.source_id);
    }
    this.enqueueLifecycle(async () => {
      let removed = 0;
      try {
        for (const source of sources) {
          await this.core.removeSource(source.source_id);
          removed += 1;
        }
        new Notice(
          removed === 1
            ? "原文已删除，知识库来源与索引已永久移除"
            : `原文件夹已删除，${removed} 个知识库来源与索引已永久移除`,
        );
      } finally {
        for (const source of sources) {
          this.pendingLifecycleSourceIds.delete(source.source_id);
        }
        await this.refreshKnowledgeState();
      }
    });
  }

  private handleRenamedPath(file: TAbstractFile, oldPath: string): void {
    const sources = this.sourcesForPath(oldPath);
    if (sources.length === 0) {
      return;
    }
    for (const source of sources) {
      this.pendingLifecycleSourceIds.add(source.source_id);
    }
    this.enqueueLifecycle(async () => {
      let moved = 0;
      let removed = 0;
      try {
        for (const source of sources) {
          const suffix = source.relative_path.slice(oldPath.length);
          const nextPath = `${file.path}${suffix}`;
          if (!nextPath.toLowerCase().endsWith(".md")) {
            await this.core.removeSource(source.source_id);
            removed += 1;
            continue;
          }
          await this.core.moveSource(source.source_id, nextPath);
          await this.core.syncSource(source.source_id);
          moved += 1;
        }
        if (moved > 0) {
          new Notice(
            moved === 1
              ? "文档路径已更新，知识库已同步"
              : `${moved} 个文档路径已更新，知识库已同步`,
          );
        }
        if (removed > 0) {
          new Notice(
            `${removed} 个非 Markdown 来源已从知识库永久移除`,
          );
        }
      } finally {
        for (const source of sources) {
          this.pendingLifecycleSourceIds.delete(source.source_id);
        }
        await this.refreshKnowledgeState();
      }
    });
  }

  private sourcesForPath(path: string): SourceStatus[] {
    const prefix = `${path}/`;
    return [...this.sourcesByPath.values()].filter(
      (source) =>
        !this.pendingLifecycleSourceIds.has(source.source_id) &&
        (source.relative_path === path ||
          source.relative_path.startsWith(prefix)),
    );
  }

  private enqueueLifecycle(task: () => Promise<void>): void {
    this.lifecycleQueue = this.lifecycleQueue.then(task).catch((error) => {
      new Notice(
        `知识库文件生命周期处理失败：${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    });
  }

  private startTreeObserver(): void {
    if (this.treeObserver) {
      return;
    }
    this.treeObserver = new MutationObserver(() => {
      this.scheduleTreeDecoration();
    });
    this.treeObserver.observe(this.app.workspace.containerEl, {
      childList: true,
      subtree: true,
    });
    this.scheduleTreeDecoration();
  }

  private stopTreeObserver(): void {
    this.treeObserver?.disconnect();
    this.treeObserver = null;
    if (this.treeDecorationFrame !== null) {
      window.cancelAnimationFrame(this.treeDecorationFrame);
      this.treeDecorationFrame = null;
    }
    this.app.workspace.containerEl
      .querySelectorAll<HTMLElement>(".ja-kb-tree-indexed")
      .forEach((element) => element.removeClass("ja-kb-tree-indexed"));
    this.app.workspace.containerEl
      .querySelectorAll<HTMLElement>(".ja-kb-tree-j-icon")
      .forEach((element) => element.remove());
  }

  private scheduleTreeDecoration(): void {
    if (this.treeDecorationFrame !== null) {
      return;
    }
    this.treeDecorationFrame = window.requestAnimationFrame(() => {
      this.treeDecorationFrame = null;
      this.decorateFileTree();
    });
  }

  private decorateFileTree(): void {
    const titles =
      this.app.workspace.containerEl.querySelectorAll<HTMLElement>(
        '.workspace-leaf-content[data-type="file-explorer"] .nav-file-title[data-path]',
      );
    titles.forEach((title) => {
      const path = title.getAttribute("data-path") ?? "";
      const indexed = this.sourcesByPath.has(path);
      title.toggleClass("ja-kb-tree-indexed", indexed);
      const currentIcon = title.querySelector(
        ".ja-kb-tree-j-icon",
      ) as HTMLImageElement | null;
      if (!indexed) {
        currentIcon?.remove();
        return;
      }
      if (currentIcon) {
        return;
      }
      const content = title.querySelector(
        ".nav-file-title-content",
      ) as HTMLElement | null;
      if (!content) {
        return;
      }
      const icon = document.createElement("img");
      icon.className = "ja-kb-tree-j-icon";
      icon.src = this.iconResourcePath();
      icon.alt = "";
      icon.setAttribute("aria-hidden", "true");
      content.before(icon);
    });
  }

  private async syncCurrentFile(file: TFile): Promise<void> {
    try {
      const status = await this.loadKnowledgeStatus();
      const source = status.sources.find(
        (candidate) => candidate.relative_path === file.path,
      );
      if (!source) {
        new TrackSourceModal(this.app, this, file).open();
        return;
      }
      await this.core.syncSource(source.source_id);
      new Notice("当前文档已同步");
      await this.refreshKnowledgeState();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : String(error));
    }
  }
}

import { existsSync } from "node:fs";
import path from "node:path";

import {
  FileSystemAdapter,
  ItemView,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  type App,
  type WorkspaceLeaf,
} from "obsidian";

import { ExportServerManager } from "./server-manager";

const VIEW_TYPE = "r3-markdown-export-view";
type ViewMode = "popout" | "tab";

interface ExportPluginSettings {
  exporterExecutable: string;
  configPath: string;
  defaultViewMode: ViewMode;
}

const DEFAULT_SETTINGS: ExportPluginSettings = {
  exporterExecutable: "markdown-export",
  configPath: "",
  defaultViewMode: "popout",
};

class MarkdownExportView extends ItemView {
  private attached = false;
  private holdsServer = false;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly plugin: MarkdownExportPlugin,
  ) {
    super(leaf);
  }

  override getViewType(): string {
    return VIEW_TYPE;
  }

  override getDisplayText(): string {
    return "R3 Markdown Export";
  }

  override getIcon(): string {
    return "file-output";
  }

  override async onOpen(): Promise<void> {
    this.attached = true;
    await this.loadExporter();
  }

  override async onClose(): Promise<void> {
    this.attached = false;
    if (this.holdsServer) {
      this.holdsServer = false;
      await this.plugin.server.release();
    }
  }

  showServerFailure(message: string): void {
    this.holdsServer = false;
    this.renderError(message);
  }

  refreshFromDisk(): void {
    const url = this.plugin.server.url;
    if (this.holdsServer && url !== null) {
      this.renderFrame(url);
      return;
    }
    void this.loadExporter();
  }

  private async loadExporter(): Promise<void> {
    this.renderStatus("Starting the exporter…");
    try {
      const url = await this.plugin.server.acquire();
      if (!this.attached) {
        await this.plugin.server.release();
        return;
      }
      this.holdsServer = true;
      this.renderFrame(url);
    } catch (error) {
      if (!this.attached) return;
      const message = error instanceof Error ? error.message : String(error);
      this.renderError(message);
      new Notice(`R3 Markdown Export could not start: ${message}`);
    }
  }

  private prepareContent(): HTMLElement {
    const container = this.contentEl;
    container.empty();
    container.addClass("r3-markdown-export-view");
    return container;
  }

  private renderStatus(message: string): void {
    const status = this.prepareContent().createDiv({ cls: "r3-markdown-export-status" });
    status.createDiv({ text: message });
  }

  private renderFrame(url: string): void {
    const frame = this.prepareContent().createEl("iframe", {
      cls: "r3-markdown-export-frame",
      attr: {
        title: "R3 Markdown Export",
        src: url,
        sandbox: "allow-forms allow-same-origin allow-scripts",
        referrerpolicy: "no-referrer",
      },
    });
    frame.focus();
  }

  private renderError(message: string): void {
    const status = this.prepareContent().createDiv({ cls: "r3-markdown-export-status" });
    status.createDiv({ cls: "r3-markdown-export-error", text: message });
    const retry = status.createEl("button", { text: "Retry", cls: "mod-cta" });
    retry.addEventListener("click", () => void this.loadExporter());
  }
}

class ExportSettingTab extends PluginSettingTab {
  constructor(
    app: App,
    private readonly plugin: MarkdownExportPlugin,
  ) {
    super(app, plugin);
  }

  override display(): void {
    this.containerEl.empty();
    this.containerEl.createEl("h2", { text: "R3 Markdown Export" });
    this.containerEl.createEl("p", {
      text: "Configure the system integration here. Export options and profiles are managed inside the exporter.",
    });

    this.containerEl.createEl("h3", { text: "General" });
    new Setting(this.containerEl)
      .setName("Default opening mode")
      .setDesc("Mode used by the ribbon icon.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("popout", "Pop-out window")
          .addOption("tab", "Tab")
          .setValue(this.plugin.settings.defaultViewMode)
          .onChange(async (value) => {
            this.plugin.settings.defaultViewMode = value === "tab" ? "tab" : "popout";
            await this.plugin.saveSettings();
          }),
      );

    this.containerEl.createEl("h3", { text: "Environment" });
    let executableDraft = this.plugin.settings.exporterExecutable;
    new Setting(this.containerEl)
      .setName("Markdown Export executable")
      .setDesc("Command name or absolute path. Install it with pipx before using the plugin.")
      .addText((text) => {
        text
          .setPlaceholder("markdown-export")
          .setValue(this.plugin.settings.exporterExecutable)
          .onChange((value) => {
            executableDraft = value;
          });
      })
      .addButton((button) =>
        button.setButtonText("Apply").onClick(async () => {
          await this.plugin.applyRuntimeSettings({
            exporterExecutable: executableDraft.trim() || DEFAULT_SETTINGS.exporterExecutable,
          });
          this.display();
        }),
      );

    let configDraft = this.plugin.settings.configPath;
    new Setting(this.containerEl)
      .setName("Configuration file")
      .setDesc(
        "Absolute path or path relative to the vault. Leave empty to discover markdown-export.toml or use built-in defaults.",
      )
      .addText((text) => {
        text
          .setPlaceholder("Automatic discovery")
          .setValue(this.plugin.settings.configPath)
          .onChange((value) => {
            configDraft = value;
          });
      })
      .addButton((button) =>
        button.setButtonText("Apply").onClick(async () => {
          await this.plugin.applyRuntimeSettings({ configPath: configDraft.trim() });
          this.display();
        }),
      );

    const resolved = this.containerEl.createDiv({ cls: "markdown-export-runtime-paths" });
    resolved.createEl("div", {
      text: `Effective configuration: ${this.plugin.resolvedConfigPath || "built-in defaults"}`,
    });
    this.containerEl.createEl("h3", { text: "Diagnostics" });
    const status = this.containerEl.createDiv({
      cls: "markdown-export-diagnostic",
      text: this.plugin.server.running ? "Server running." : "Server stopped.",
    });
    new Setting(this.containerEl)
      .setName("Check environment")
      .setDesc("Starts the exporter, validates its local protocol and stops it again when no views are open.")
      .addButton((button) =>
        button.setButtonText("Check").setCta().onClick(async () => {
          button.setDisabled(true);
          status.setText("Checking executable, configuration and server…");
          try {
            await this.plugin.checkEnvironment();
            status.setText("Environment ready. The exporter can start.");
          } catch (error) {
            status.setText(error instanceof Error ? error.message : String(error));
          } finally {
            button.setDisabled(false);
          }
        }),
      )
      .addButton((button) =>
        button.setButtonText("Restart").onClick(async () => {
          await this.plugin.recreateServerManager("The server has restarted. Select Retry.");
          status.setText("Server restarted.");
        }),
      );

    new Setting(this.containerEl)
      .setName("Copy diagnostics")
      .setDesc("Copies paths and technical state for support. It never includes vault contents.")
      .addButton((button) =>
        button.setButtonText("Copy").onClick(async () => {
          await navigator.clipboard.writeText(this.plugin.diagnosticSummary());
          new Notice("Exporter diagnostics copied.");
        }),
      );
  }
}

export default class MarkdownExportPlugin extends Plugin {
  override settings: ExportPluginSettings = DEFAULT_SETTINGS;
  server!: ExportServerManager;
  private vaultRoot = "";

  get resolvedConfigPath(): string {
    const configured = this.settings.configPath.trim();
    if (configured) {
      return path.isAbsolute(configured)
        ? path.resolve(configured)
        : path.resolve(this.vaultRoot, configured);
    }
    const projectConfig = path.join(this.vaultRoot, "markdown-export.toml");
    return existsSync(projectConfig) ? projectConfig : "";
  }

  override async onload(): Promise<void> {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      throw new Error("R3 Markdown Export requires a local desktop vault.");
    }
    this.vaultRoot = adapter.getBasePath();
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData() as Partial<ExportPluginSettings> | null) };
    this.createServerManager();

    this.registerView(VIEW_TYPE, (leaf) => new MarkdownExportView(leaf, this));
    this.addRibbonIcon("file-output", "Open R3 Markdown Export", () => {
      void this.openExporter(this.settings.defaultViewMode);
    });
    this.addCommand({
      id: "open-popout",
      name: "Open exporter in a pop-out window",
      callback: () => void this.openExporter("popout"),
    });
    this.addCommand({
      id: "open-tab",
      name: "Open exporter in a tab",
      callback: () => void this.openExporter("tab"),
    });
    this.addSettingTab(new ExportSettingTab(this.app, this));
  }

  override async onunload(): Promise<void> {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
    await this.server.stopAll();
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  async applyRuntimeSettings(
    changes: Partial<Pick<ExportPluginSettings, "exporterExecutable" | "configPath">>,
  ): Promise<void> {
    const next = { ...this.settings, ...changes };
    if (
      next.exporterExecutable === this.settings.exporterExecutable
      && next.configPath === this.settings.configPath
    ) return;
    this.settings = next;
    await this.saveSettings();
    await this.recreateServerManager(
      "The environment configuration has changed. Select Retry.",
    );
  }

  async checkEnvironment(): Promise<void> {
    await this.server.check();
  }

  diagnosticSummary(): string {
    return [
      "R3 Markdown Export",
      `Plugin: ${this.manifest.version}`,
      `Executable: ${this.settings.exporterExecutable}`,
      `Vault: ${this.vaultRoot}`,
      `Configuration: ${this.resolvedConfigPath || "built-in defaults"}`,
      `Server: ${this.server.running ? this.server.url : "stopped"}`,
    ].join("\n");
  }

  async recreateServerManager(message: string): Promise<void> {
    await this.server.stopAll();
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof MarkdownExportView) {
        leaf.view.showServerFailure(message);
      }
    }
    this.createServerManager();
  }

  private createServerManager(): void {
    this.server = new ExportServerManager({
      exporterExecutable: this.settings.exporterExecutable,
      vaultRoot: this.vaultRoot,
      configPath: this.resolvedConfigPath || undefined,
      onUnexpectedExit: (message) => {
        new Notice(message);
        for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
          if (leaf.view instanceof MarkdownExportView) leaf.view.showServerFailure(message);
        }
      },
    });
  }

  private async openExporter(mode: ViewMode): Promise<void> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE).find((leaf) => {
      const state = leaf.getViewState().state;
      return typeof state === "object" && state !== null && "mode" in state && state.mode === mode;
    });
    if (existing !== undefined) {
      await this.app.workspace.revealLeaf(existing);
      if (existing.view instanceof MarkdownExportView) {
        existing.view.refreshFromDisk();
      }
      return;
    }

    const leaf =
      mode === "popout"
        ? this.app.workspace.openPopoutLeaf()
        : this.app.workspace.getLeaf("tab");
    await leaf.setViewState({
      type: VIEW_TYPE,
      active: true,
      state: { mode },
    });
    await this.app.workspace.revealLeaf(leaf);
  }
}

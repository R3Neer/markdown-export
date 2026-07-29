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

const VIEW_TYPE = "mud-markdown-export-view";
type ViewMode = "popout" | "tab";

interface ExportPluginSettings {
  pythonExecutable: string;
}

const DEFAULT_SETTINGS: ExportPluginSettings = {
  pythonExecutable: "python",
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
    return "MUD Markdown Export";
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

  private async loadExporter(): Promise<void> {
    this.renderStatus("Iniciando el exportador…");
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
      new Notice(`No se pudo iniciar el exportador Markdown: ${message}`);
    }
  }

  private prepareContent(): HTMLElement {
    const container = this.contentEl;
    container.empty();
    container.addClass("mud-markdown-export-view");
    return container;
  }

  private renderStatus(message: string): void {
    const status = this.prepareContent().createDiv({ cls: "mud-markdown-export-status" });
    status.createDiv({ text: message });
  }

  private renderFrame(url: string): void {
    const frame = this.prepareContent().createEl("iframe", {
      cls: "mud-markdown-export-frame",
      attr: {
        title: "Exportador Markdown portable",
        src: url,
        sandbox: "allow-forms allow-same-origin allow-scripts",
        referrerpolicy: "no-referrer",
      },
    });
    frame.focus();
  }

  private renderError(message: string): void {
    const status = this.prepareContent().createDiv({ cls: "mud-markdown-export-status" });
    status.createDiv({ cls: "mud-markdown-export-error", text: message });
    const retry = status.createEl("button", { text: "Reintentar", cls: "mod-cta" });
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
    new Setting(this.containerEl)
      .setName("Ejecutable de Python")
      .setDesc("Comando o ruta absoluta que inicia Python 3.11 o posterior.")
      .addText((text) =>
        text
          .setPlaceholder("python")
          .setValue(this.plugin.settings.pythonExecutable)
          .onChange(async (value) => {
            const executable = value.trim() || DEFAULT_SETTINGS.pythonExecutable;
            this.plugin.settings.pythonExecutable = executable;
            await this.plugin.saveData(this.plugin.settings);
            await this.plugin.recreateServerManager();
          }),
      );
  }
}

export default class MarkdownExportPlugin extends Plugin {
  override settings: ExportPluginSettings = DEFAULT_SETTINGS;
  server!: ExportServerManager;
  private vaultRoot = "";

  override async onload(): Promise<void> {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      throw new Error("MUD Markdown Export necesita una bóveda local de escritorio.");
    }
    this.vaultRoot = adapter.getBasePath();
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData() as Partial<ExportPluginSettings> | null) };
    this.createServerManager();

    this.registerView(VIEW_TYPE, (leaf) => new MarkdownExportView(leaf, this));
    this.addRibbonIcon("file-output", "Abrir exportador Markdown", () => {
      void this.openExporter("popout");
    });
    this.addCommand({
      id: "open-markdown-export-popout",
      name: "Abrir exportador en ventana flotante",
      callback: () => void this.openExporter("popout"),
    });
    this.addCommand({
      id: "open-markdown-export-tab",
      name: "Abrir exportador en pestaña",
      callback: () => void this.openExporter("tab"),
    });
    this.addSettingTab(new ExportSettingTab(this.app, this));
  }

  override async onunload(): Promise<void> {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
    await this.server.stopAll();
  }

  async recreateServerManager(): Promise<void> {
    await this.server.stopAll();
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof MarkdownExportView) {
        leaf.view.showServerFailure("La configuración de Python ha cambiado. Pulsa Reintentar.");
      }
    }
    this.createServerManager();
  }

  private createServerManager(): void {
    this.server = new ExportServerManager({
      pythonExecutable: this.settings.pythonExecutable,
      vaultRoot: this.vaultRoot,
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

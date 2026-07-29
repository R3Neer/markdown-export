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

const VIEW_TYPE = "mud-markdown-export-view";
type ViewMode = "popout" | "tab";

interface ExportPluginSettings {
  pythonExecutable: string;
  configPath: string;
  defaultViewMode: ViewMode;
}

const DEFAULT_SETTINGS: ExportPluginSettings = {
  pythonExecutable: "python",
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
    return "Exportador Markdown";
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
        title: "Exportador Markdown",
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
    this.containerEl.createEl("h2", { text: "Exportador Markdown" });
    this.containerEl.createEl("p", {
      text: "Configura solo la integración con el sistema. Las opciones de exportación y los perfiles se gestionan dentro del exportador.",
    });

    this.containerEl.createEl("h3", { text: "General" });
    new Setting(this.containerEl)
      .setName("Apertura predeterminada")
      .setDesc("Modo usado por el icono de la cinta.")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("popout", "Ventana flotante")
          .addOption("tab", "Pestaña")
          .setValue(this.plugin.settings.defaultViewMode)
          .onChange(async (value) => {
            this.plugin.settings.defaultViewMode = value === "tab" ? "tab" : "popout";
            await this.plugin.saveSettings();
          }),
      );

    this.containerEl.createEl("h3", { text: "Entorno" });
    let pythonDraft = this.plugin.settings.pythonExecutable;
    new Setting(this.containerEl)
      .setName("Ejecutable de Python")
      .setDesc("Comando o ruta absoluta de Python 3.11 o posterior. Los cambios se aplican únicamente al pulsar Aplicar.")
      .addText((text) => {
        text
          .setPlaceholder("python")
          .setValue(this.plugin.settings.pythonExecutable)
          .onChange((value) => {
            pythonDraft = value;
          });
      })
      .addButton((button) =>
        button.setButtonText("Aplicar").onClick(async () => {
          await this.plugin.applyRuntimeSettings({
            pythonExecutable: pythonDraft.trim() || DEFAULT_SETTINGS.pythonExecutable,
          });
          this.display();
        }),
      );

    let configDraft = this.plugin.settings.configPath;
    new Setting(this.containerEl)
      .setName("Archivo de configuración")
      .setDesc(
        "Ruta absoluta o relativa a la bóveda. Vacío: usa la configuración del proyecto si existe y, en caso contrario, la configuración genérica incluida.",
      )
      .addText((text) => {
        text
          .setPlaceholder("Detección automática")
          .setValue(this.plugin.settings.configPath)
          .onChange((value) => {
            configDraft = value;
          });
      })
      .addButton((button) =>
        button.setButtonText("Aplicar").onClick(async () => {
          await this.plugin.applyRuntimeSettings({ configPath: configDraft.trim() });
          this.display();
        }),
      );

    const resolved = this.containerEl.createDiv({ cls: "markdown-export-runtime-paths" });
    resolved.createEl("div", {
      text: `Configuración efectiva: ${this.plugin.resolvedConfigPath}`,
    });
    resolved.createEl("div", {
      text: `Motor incluido: ${this.plugin.runtimeRoot}`,
    });

    this.containerEl.createEl("h3", { text: "Diagnóstico" });
    const status = this.containerEl.createDiv({
      cls: "markdown-export-diagnostic",
      text: this.plugin.server.running ? "Servidor en ejecución." : "Servidor detenido.",
    });
    new Setting(this.containerEl)
      .setName("Comprobar entorno")
      .setDesc("Inicia el motor, valida el protocolo local y vuelve a detenerlo si no hay vistas abiertas.")
      .addButton((button) =>
        button.setButtonText("Comprobar").setCta().onClick(async () => {
          button.setDisabled(true);
          status.setText("Comprobando Python, configuración y servidor…");
          try {
            await this.plugin.checkEnvironment();
            status.setText("Entorno correcto. El exportador puede iniciarse.");
          } catch (error) {
            status.setText(error instanceof Error ? error.message : String(error));
          } finally {
            button.setDisabled(false);
          }
        }),
      )
      .addButton((button) =>
        button.setButtonText("Reiniciar").onClick(async () => {
          await this.plugin.recreateServerManager("El servidor se ha reiniciado. Pulsa Reintentar.");
          status.setText("Servidor reiniciado.");
        }),
      );

    new Setting(this.containerEl)
      .setName("Copiar diagnóstico")
      .setDesc("Copia rutas y estado técnico para solicitar ayuda. No incluye el contenido de la bóveda.")
      .addButton((button) =>
        button.setButtonText("Copiar").onClick(async () => {
          await navigator.clipboard.writeText(this.plugin.diagnosticSummary());
          new Notice("Diagnóstico del exportador copiado.");
        }),
      );
  }
}

export default class MarkdownExportPlugin extends Plugin {
  override settings: ExportPluginSettings = DEFAULT_SETTINGS;
  server!: ExportServerManager;
  private vaultRoot = "";
  private pluginRoot = "";

  get runtimeRoot(): string {
    return path.join(this.pluginRoot, "python");
  }

  get resolvedConfigPath(): string {
    const configured = this.settings.configPath.trim();
    if (configured) {
      return path.isAbsolute(configured)
        ? path.resolve(configured)
        : path.resolve(this.vaultRoot, configured);
    }
    const projectConfig = path.join(
      this.vaultRoot,
      "tooling",
      "markdown_export",
      "profiles.toml",
    );
    return existsSync(projectConfig)
      ? projectConfig
      : path.join(this.runtimeRoot, "tooling", "markdown_export", "profiles.toml");
  }

  override async onload(): Promise<void> {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      throw new Error("Markdown Export necesita una bóveda local de escritorio.");
    }
    this.vaultRoot = adapter.getBasePath();
    this.pluginRoot = path.resolve(
      this.vaultRoot,
      this.manifest.dir ?? `.obsidian/plugins/${this.manifest.id}`,
    );
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData() as Partial<ExportPluginSettings> | null) };
    this.createServerManager();

    this.registerView(VIEW_TYPE, (leaf) => new MarkdownExportView(leaf, this));
    this.addRibbonIcon("file-output", "Abrir exportador Markdown", () => {
      void this.openExporter(this.settings.defaultViewMode);
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

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  async applyRuntimeSettings(
    changes: Partial<Pick<ExportPluginSettings, "pythonExecutable" | "configPath">>,
  ): Promise<void> {
    const next = { ...this.settings, ...changes };
    if (
      next.pythonExecutable === this.settings.pythonExecutable
      && next.configPath === this.settings.configPath
    ) return;
    this.settings = next;
    await this.saveSettings();
    await this.recreateServerManager(
      "La configuración del entorno ha cambiado. Pulsa Reintentar.",
    );
  }

  async checkEnvironment(): Promise<void> {
    await this.server.check();
  }

  diagnosticSummary(): string {
    return [
      "Markdown Export",
      `Plugin: ${this.manifest.version}`,
      `Python: ${this.settings.pythonExecutable}`,
      `Bóveda: ${this.vaultRoot}`,
      `Motor: ${this.runtimeRoot}`,
      `Configuración: ${this.resolvedConfigPath}`,
      `Servidor: ${this.server.running ? this.server.url : "detenido"}`,
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
      pythonExecutable: this.settings.pythonExecutable,
      vaultRoot: this.vaultRoot,
      runtimeRoot: this.runtimeRoot,
      configPath: this.resolvedConfigPath,
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

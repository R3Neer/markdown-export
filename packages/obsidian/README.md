# R3 Markdown Export for Obsidian

This desktop adapter opens the R3 Markdown Export web interface in an Obsidian
tab or pop-out window. It requires the separately installed `markdown-export`
command and never installs or updates that dependency itself.

The adapter validates the loopback server, protocol and vault root before
loading a sandboxed iframe. Settings select the default view, executable path
and an optional TOML configuration.

Development and installation commands are documented in the repository root.

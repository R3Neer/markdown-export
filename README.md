# R3 Markdown Export

R3 Markdown Export turns selected Markdown files, folders or reusable profiles
into portable documents. It understands Obsidian wikilinks, can follow linked
documents, can remove frontmatter and can preserve a source tree in a ZIP.

The exporter works as a command-line tool, a local web interface and a desktop
Obsidian integration. It reads and writes local files only: there is no
telemetry and no network service. The web interface binds exclusively to
`127.0.0.1` and protects changes with a per-session token.

## Install

Python 3.11 or later is required. The 0.1.1 release is distributed through
GitHub rather than PyPI:

```console
pipx install https://github.com/R3Neer/markdown-export/releases/download/v0.1.1/r3_markdown_export-0.1.1-py3-none-any.whl
```

Confirm the installation:

```console
markdown-export --version
markdown-export --help
```

## Use

Run the exporter from a document tree or provide an explicit root:

```console
markdown-export list-profiles
markdown-export export --files README.md docs --name project-context
markdown-export export --profile current --follow-links
markdown-export export --files docs README.md --zip-tree
markdown-export serve
```

Exports are written atomically to `exports/` by default. The exporter never
writes outside the selected root.

## Configuration

The command discovers `markdown-export.toml` in the effective root. An explicit
`--config` takes precedence; without either, the built-in profile selects all
Markdown below the current directory.

```toml
[export]
root = "."
output_dir = "exports"
default_profile = "current"
exclude = ["private/**"]
source_languages = { yaml = "yaml", toml = "toml" }

[profiles.current]
title = "Current documentation"
include = ["README.md", "docs"]
follow_links = false
strip_frontmatter = true
source_markers = true
strict_links = false
max_chars = 0
```

Personal profiles created in the web interface are stored beside an explicit
configuration. When built-in defaults are active they are stored as
`.markdown-export.local.toml` in the root. This file is intended to remain
uncommitted.

## Obsidian

The R3 Markdown Export plugin opens the local web interface in a tab or pop-out
window. Install the `markdown-export` command first, then install the three
plugin release files under `.obsidian/plugins/r3-markdown-export/` and enable
the plugin.

The plugin can use a command name from `PATH` or an absolute executable path.
Its environment check verifies process start-up, the loopback address, the
protocol version and the selected vault before showing any content.

## Markdown behaviour

The exporter preserves fenced code, callouts, LaTeX and tables. It rewrites
links between included documents to internal anchors and reports existing but
omitted documents separately. External links are left unchanged. Additional
plain-text source formats can be wrapped in fenced blocks through
`source_languages`.

Attachments are currently diagnosed but not copied. Strict-link mode rejects
unresolved, ambiguous and attachment links. The tool produces Markdown rather
than rendering HTML.

## Maintaining R3 Markdown Export

These commands are for contributors changing the project itself. From a local
clone, install the Python package and its tests in editable mode:

```console
python -m pip install -e ".[test,build]"
pytest
```

Install the Node.js 22 dependencies and validate the Obsidian adapter:

```console
npm ci
npm run check
```

To install the adapter into a development vault:

```console
npm run install-local -- --vault /path/to/vault
```

See [the architecture notes](docs/architecture.md) for component boundaries and
the local protocol.

## Licence

R3 Markdown Export is available under the [MIT Licence](LICENSE).

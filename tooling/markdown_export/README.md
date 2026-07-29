# Exportador Markdown portable

Herramienta de Python 3.11 o posterior para seleccionar, combinar y hacer portable el Markdown de una bóveda. El motor no depende de MUD: los perfiles incluidos sí describen esta bóveda.

Procede conceptualmente de `export_canon_core.py` y `export_canon_web.py`, creados para otro proyecto. Esta versión no modifica esos scripts y elimina su acoplamiento a Canon, `Outils` y sus convenciones.

## Uso

No requiere instalar dependencias:

```powershell
python -m tooling.markdown_export list-profiles
python -m tooling.markdown_export export --profile decisions
python -m tooling.markdown_export export --files ruta1.md carpeta --name mud-context
python -m tooling.markdown_export export --profile specification --follow-links
python -m tooling.markdown_export export --files especificacion README.md --zip-tree
python -m tooling.markdown_export serve
```

Los archivos se escriben atómicamente en `exports/`, que Git ignora por ser una carpeta de artefactos derivados.

Opciones comunes de exportación:

- `--root`: raíz alternativa de la bóveda.
- `--config`: TOML alternativo.
- `--output`: salida concreta, siempre dentro de la raíz.
- `--follow-links` o `--no-follow-links`.
- `--strip-frontmatter` o `--keep-frontmatter`.
- `--source-markers` o `--no-source-markers`.
- `--strict-links`: aborta ante enlaces ambiguos, inexistentes o adjuntos.
- `--max-chars`: divide entre documentos, nunca dentro de uno. Una sola parte
  produce `nombre.md`; varias partes se empaquetan como `nombre.zip`.
- `--zip-tree`: produce siempre un ZIP con cada fuente en un archivo separado y
  conserva su ruta relativa desde la raíz. Las dependencias descubiertas con
  `--follow-links` también se incluyen. En este modo se puede retirar el
  frontmatter, pero no se añaden cabeceras, marcadores ni se reescriben enlaces.
- `--timestamp`: añade una fecha; sin ella, el resultado es reproducible para el mismo contenido, configuración y commit.

`serve` acepta `--port` y `--no-browser`. El servidor escucha exclusivamente en `127.0.0.1`; no debe exponerse como servicio de red.

La integración local de Obsidian utiliza además `--port 0 --ready-json`: el sistema elige un puerto libre y emite un mensaje JSON que el plugin valida mediante `/api/health`. Véase `tooling/obsidian/markdown-export/README.md`.

## Perfiles incluidos

- `specification`: especificación formal.
- `decisions`: registro, preguntas abiertas y ADR.
- `language`: semántica vigente del lenguaje, sin decisiones de arquitectura o producto.
- `current`: corpus vigente completo, salvo referencias retiradas y aprendizaje histórico.

Los perfiles conservan su orden explícito y no siguen enlaces por defecto. Las dependencias descubiertas con `--follow-links` se añaden una sola vez y en orden determinista.

## Configuración TOML

```toml
[export]
root = "../.."
output_dir = "exports"
default_profile = "current"
exclude = ["privado/**"]

[profiles.example]
title = "Título del documento"
include = ["docs/**/*.md", "README.md"]
exclude = ["docs/historico/**"]
follow_links = false
strip_frontmatter = true
source_markers = true
strict_links = false
max_chars = 0
```

Las rutas son relativas a `root`. Siempre se excluyen `.git/`, `.obsidian/`, `.trash/` y `exports/`, aunque otro TOML no las mencione.

## Tratamiento del Markdown

El exportador:

- lee UTF-8 con o sin BOM y escribe UTF-8 con saltos LF;
- retira frontmatter por defecto;
- conserva callouts, LaTeX, tablas y código cercado;
- normaliza encabezados y crea anclas HTML derivadas de ruta y encabezado;
- convierte enlaces a documentos incluidos en enlaces internos;
- registra documentos existentes pero omitidos en un apéndice;
- diagnostica destinos ambiguos o inexistentes;
- deja intactos los enlaces externos;
- obtiene el commit Git de origen cuando está disponible.

La resolución prueba, por este orden, la ruta desde la raíz, la ruta relativa al documento y un basename único.

## Interfaz web

La interfaz muestra todos los Markdown admitidos, permite buscar y seleccionar parcialmente, cargar un perfil, previsualizar dependencias, partes, caracteres y advertencias, y finalmente exportar. Si se modifica la selección o cualquier opción, el selector pasa a **Personalizado (sin guardar)**. **Guardar como perfil…** conserva esa configuración en `profiles.local.toml`, que Git ignora. La opción **ZIP con archivos separados (conservar carpetas)** genera una instantánea navegable de los documentos en vez de combinarlos. Cada POST requiere un token de sesión. Los nombres de archivos se insertan en la página mediante `textContent` o nodos de texto.

## Limitaciones de la primera versión

No copia imágenes ni otros adjuntos. Los detecta, advierte y, en modo estricto, cancela la operación. Tampoco renderiza Markdown a HTML: produce Markdown portable para personas y para intercambio de contexto con otras herramientas.

## Pruebas

```powershell
python -m unittest discover -s tooling/markdown_export/tests
```

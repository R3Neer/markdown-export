# MUD Markdown Export

Plugin local de escritorio para abrir el exportador Markdown portable dentro de Obsidian. El icono lateral abre una ventana flotante; la paleta de comandos permite elegir entre ventana flotante y pestaña integrada.

El plugin no implementa la exportación. Inicia bajo demanda el servidor de `tooling.markdown_export`, verifica su protocolo y presenta su interfaz en un `iframe` local.

## Requisitos

- Obsidian 1.7.2 o posterior.
- Node.js para compilar o instalar el plugin.
- Python 3.11 o posterior para utilizar el exportador.
- Una bóveda local de escritorio que contenga el paquete `tooling.markdown_export`.

## Desarrollo

```powershell
npm install
npm run check
npm run install-local
```

`install-local` compila el plugin, copia `main.js`, `manifest.json` y `styles.css` a `.obsidian/plugins/mud-markdown-export/`, y añade su identificador a `community-plugins.json` sin retirar otros plugins. Después hay que recargar Obsidian.

La fuente y el lockfile se versionan. `node_modules/`, `dist/` y la instalación bajo `.obsidian/` son derivados locales.

## Uso

- Icono **file-output**: abre o recupera la ventana flotante.
- Comando **Abrir exportador en ventana flotante**.
- Comando **Abrir exportador en pestaña**.

El primer uso arranca un único servidor Python. Todas las vistas lo comparten y se detiene al cerrar la última. Si Python no está en `PATH`, indica su ruta absoluta en los ajustes del plugin.

## Seguridad y privacidad

- El servidor escucha solo en `127.0.0.1`.
- Cada sesión mantiene el token POST del exportador.
- El `iframe` restringe navegación y capacidades mediante `sandbox`.
- No hay telemetría ni tráfico externo durante el uso.
- Solo se conservan las últimas veinte líneas de error del proceso para informar de fallos.

## Diagnóstico

- **Python no existe:** configura el ejecutable; por ejemplo, `C:\Python314\python.exe`.
- **Timeout:** ejecuta manualmente el comando mostrado en la arquitectura del servidor y revisa su error.
- **Versión incompatible:** actualiza conjuntamente el plugin y `tooling.markdown_export`.
- **La vista quedó abierta tras una caída:** pulsa **Reintentar**; se creará un proceso nuevo.

## Arquitectura

El servidor anuncia por `stdout` una URL de puerto dinámico y la versión 1 del protocolo. El plugin valida el mensaje, exige `127.0.0.1`, consulta `/api/health` y solo entonces carga el `iframe`. Un contador de consumidores gobierna el proceso: primera vista lo crea y última vista lo termina.

# Markdown Export

Plugin local de escritorio para abrir el exportador Markdown portable dentro de Obsidian. El icono lateral abre una ventana flotante; la paleta de comandos permite elegir entre ventana flotante y pestaña integrada.

El plugin incluye el motor Python del exportador, lo inicia bajo demanda, verifica su protocolo y presenta su interfaz en un `iframe` local. No exige que la bóveda contenga el código fuente del motor.

## Requisitos

- Obsidian 1.7.2 o posterior.
- Node.js para compilar o instalar el plugin.
- Python 3.11 o posterior para utilizar el exportador.
- Una bóveda local de escritorio.

## Desarrollo

```powershell
npm install
npm run check
npm run install-local
```

`install-local` compila el plugin, copia `main.js`, `manifest.json`, `styles.css`, el motor Python y una configuración genérica a `.obsidian/plugins/mud-markdown-export/`, y añade su identificador a `community-plugins.json` sin retirar otros plugins. Después hay que recargar Obsidian.

La fuente y el lockfile se versionan. `node_modules/`, `dist/` y la instalación bajo `.obsidian/` son derivados locales.

## Uso

- Icono **file-output**: abre o recupera la ventana flotante.
- Comando **Abrir exportador en ventana flotante**.
- Comando **Abrir exportador en pestaña**.

El primer uso arranca un único servidor Python. Todas las vistas lo comparten y se detiene al cerrar la última.

## Ajustes

- **Apertura predeterminada:** el icono puede abrir una ventana flotante o una pestaña.
- **Ejecutable de Python:** comando o ruta absoluta. Solo se aplica al pulsar el botón correspondiente.
- **Archivo de configuración:** ruta absoluta o relativa a la bóveda. Vacío activa la detección automática.
- **Comprobar entorno:** valida Python, configuración, arranque y protocolo.
- **Reiniciar:** detiene el servidor y permite reconstruirlo con la configuración actual.
- **Copiar diagnóstico:** copia versión, rutas y estado, pero no contenido de la bóveda.

La detección automática usa `tooling/markdown_export/profiles.toml` cuando la bóveda lo contiene; de lo contrario utiliza la configuración genérica incluida, cuyo perfil inicial selecciona todo el Markdown. Un tercero puede empezar a exportar sin preparar estructura adicional y después guardar perfiles personales desde la propia interfaz.

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

El servidor incluido anuncia por `stdout` una URL de puerto dinámico y la versión 1 del protocolo. El plugin valida el mensaje, exige `127.0.0.1`, consulta `/api/health` y solo entonces carga el `iframe`. Un contador de consumidores gobierna el proceso: primera vista lo crea y última vista lo termina.

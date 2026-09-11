# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Unreleased]

### Added

- **Sueño nocturno** (`NIGHTLY_SLEEP_MINUTES`, 0 = desactivado): a
  horario fijo, si está en pausa, le devuelve a la Mac el permiso de
  dormir unos minutos y programa que despierte sola — mitiga el colgado
  de periféricos (Touch Bar) tras uptimes muy largos con
  `disablesleep=1`. Nunca corre si está vigilando. Verifica que el
  despertar quedó agendado antes de dormir (si no, no duerme); un hilo
  aparte reafirma `disablesleep=1` cada 5 min fuera de la ventana como
  red de seguridad. `bin/ojota night-sleep` para probarlo a mano.

### Fixed

- `ojota status`: un fallo transitorio del heartbeat que ya se recuperó
  aparecía bajo "errores". Ahora hay una fila `heartbeat` propia con el
  estado actual (al día / sin conexión desde cuándo) y esas líneas no
  ensucian "errores" ni "mantenimiento".
- `ojota status`: "últimos movimientos" volvía a mostrar solo las líneas
  de detección tras el cambio de log `clip armado` → `video listo`; se
  reconoce el texto viejo hasta que rote el log.

## [0.1.0] - 2026-09-09

Primera versión etiquetada. Funcionando 24/7 como servicio; en etapa de
afinar la detección con uso real.

### Added

- **Daemon de captura y detección** (`bin/ojota-daemon.py`):
  - Segmentador RTSP con `ffmpeg -c copy` a un ring buffer de segmentos
    mpegts (video + audio, sin re-encodear) → pre-captura casi gratis.
  - Detección por diferencia de frames sobre el substream en gris a baja
    resolución y pocos fps (numpy): umbral de área, mínimo de frames,
    warm-up y descarte de cambios de luz.
  - Armado de clips mp4 con pre/post-captura vía `concat`.
  - Subida en vivo (`LIVE_UPLOAD`): armado, el ring buffer se copia a
    `gdrive:.../live/` en continuo (retención ~3 min, borrado permanente).
    Cubre el caso de que se lleven la Mac en medio de un evento. Aviso
    instantáneo al detectar, sin esperar la subida del clip.
  - Estado en `config/profile` (`armado` / `desarmado`): armado = captura
    + detección + subida + notificaciones; desarmado = frena los dos
    ffmpeg, solo siguen los chequeos de salud. Comandos `ojota salir` /
    `ojota volver` (alias `armar` / `desarmar`).
  - **Auto-armado por presencia**: el daemon pinguea `PHONE_IP`; sin
    respuesta por `PRESENCE_AWAY_MINUTES` → armado, al volver → desarmado.
    `ojota salir` a mano deja `config/manual-hold` para que la presencia
    no lo desarme. Cero apps en el celular. Al volver, los clips de
    los últimos `RETURN_GRACE_SECONDS` (sos vos entrando) van a
    `probablemente-vos/` sin notificar (no se borran).
    (Un canal de control por ntfy para geofence se implementó y luego se
    quitó por simplicidad; queda en el historial.)
  - Supervisión de subprocesos con backoff exponencial y alerta de
    cámara caída.
  - Log rotativo; credenciales redactadas en el log.
  - Rutas derivadas de la ubicación del repo (sin paths absolutos).
- **Subida y notificaciones**:
  - `bin/ojota-clip-hook.sh`: por cada clip, sube a Google Drive con
    reintentos, verifica el tamaño y solo entonces borra el local; si
    falla lo deja en `pending/`. Notifica por ntfy.sh (hora, frame del
    clip y link a Drive) respetando una ventana de silencio que agrupa
    avisos seguidos sin frenar la grabación.
  - `bin/ojota-notify.sh`: helper de notificación push (ntfy).
  - Daemon: alertas de prioridad alta — cámara sin señal (con aviso al
    recuperarse) y "muchos eventos" (`EVENT_BURST_COUNT` en
    `EVENT_BURST_MINUTES`). Hilo que reintenta las subidas pendientes.
- **`bin/ojota`**: comando de control — `salir` / `volver` (alias `armar`
  / `desarmar`), `status`, `start` / `stop` / `restart`, `logs`,
  `test-notify`, `prune`, `backup-config`, `install` / `uninstall`.
- **Mantenimiento (daemon)**: retención de clips en Drive cada
  `RETENTION_CHECK_HOURS`, backup de `config/` a `config-backup/` al
  arrancar y cada 7 días, y heartbeat opcional (`HEARTBEAT_URL`) para
  detectar cortes de luz que apaguen la Mac.
- **Servicio (LaunchDaemon)**: `deploy/com.nanotaboada.ojota.plist` +
  `bin/ojota install` / `uninstall` — corre a nivel sistema (arranca sin
  login) **como root** (obligatorio en macOS Sequoia: un daemon que baja a
  un usuario queda bloqueado por Local Network Privacy y no llega a la
  cámara). `install` copia la config de rclone a `config/rclone.conf`.
  `KeepAlive` reinicia solo ante crash; apagado interrumpible (~0.6 s).
- **Calibración** (`bin/ojota-tune.py`): muestra el % de cambio por frame
  en vivo para ajustar los umbrales.
- **Configuración** (`config/ojota.conf.example`): todos los parámetros
  documentados en un solo archivo; los secretos van en `config/ojota.conf`
  (no versionado).
- **Documentación**: `README.md` (arquitectura en Mermaid, setup,
  configuración, diagnóstico), `PRIVACY.md`, `LICENSE` (MIT),
  `docs/logo.svg`.
- Entorno Python aislado (`requirements.txt`: numpy).
- **CI** (`.github/workflows/ci.yml`): commitlint, ShellCheck sobre los
  scripts de `bin/` y byte-compile del daemon en cada push / PR. Evita
  que un error de sintaxis llegue a `main` y deje el LaunchDaemon en
  bucle de reinicio.
- **CodeQL** (`.github/workflows/codeql.yml`): análisis de seguridad de
  Python y de los workflows.
- Dependabot (`pip` + `github-actions`), `commitlint.config.mjs`,
  `.python-version`, plantillas de issue / PR y `SECURITY.md`.

### Changed

- Mensajes al usuario reescritos en lenguaje llano (sin jerga de
  proceso: "frames", "ring buffer", "detección en curso", nombres de
  carpetas internas). Pensados para reutilizarse en una app propia.
- `ojota status`: salida reformateada al estilo de los scripts de
  mantenimiento (etiquetas alineadas en gris, valores en color, tiempos
  relativos "hoy 14:29", frases en vez de líneas de log crudas).
  Presencia por ping en vivo al celu.
- El aviso de la llegada (auto-pausa) pasa a prioridad mínima.
- Segunda pasada de tono: `Ojota` con mayúscula (nombre propio, se va la
  duda de género), `vigilando` / `en pausa` en vez de `armado` /
  `desarmado` en los textos, tono según contexto (alegre en la llegada,
  "Ojo" en movimiento, preocupación en las alertas serias). Los comandos
  (`salir` / `volver` / `armar` / `desarmar`) y los valores internos de
  `config/profile` no cambian.
- README: terminología al día (`vigilando` / `en pausa`, `Ojota`),
  emojis en los títulos de sección, URL de clone real. Corregida la
  descripción de `POSTCAPTURE_SECONDS` (es la espera para cerrar el
  evento; el clip termina 2 s después del último movimiento).

### Fixed

- Ventana de "volviste": el timestamp se escribía con decimales y la
  comparación entera del hook fallaba en silencio, así que los clips de
  tu llegada iban igual a la raíz y con aviso. Ahora se escribe entero.
- `_on_return`: el `rclone move` a `probablemente-vos/` fallaba siempre
  ("overlapping remotes"). Se pasó a reglas `--filter` excluyendo el
  subdirectorio destino.
- El aviso instantáneo ("Movimiento en curso") ahora se difiere
  `INSTANT_NOTIFY_DELAY_SECONDS` (45 s) y se cancela si en ese lapso la
  presencia te reconoce — cubre la latencia de reconexión del celu al
  WiFi al entrar, que hacía llegar 2 avisos de tu propia llegada.

[Unreleased]: https://github.com/nanotaboada/ojota/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nanotaboada/ojota/releases/tag/v0.1.0

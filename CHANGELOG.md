# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Unreleased]

### Added

- **Daemon de captura y detección** (`bin/ojota-daemon.py`):
  - Segmentador RTSP con `ffmpeg -c copy` a un ring buffer de segmentos
    mpegts (video + audio, sin re-encodear) → pre-captura casi gratis.
  - Detección por diferencia de frames sobre el substream en gris a baja
    resolución y pocos fps (numpy): umbral de área, mínimo de frames,
    warm-up y descarte de cambios de luz.
  - Armado de clips mp4 con pre/post-captura vía `concat`.
  - Perfiles `casa` / `afuera` por archivo `config/profile`.
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
  - Daemon: alerta de prioridad alta si la cámara deja de responder,
    aviso al recuperarse, e hilo que reintenta las subidas pendientes.
- **`bin/ojota`**: comando de control — `casa` / `afuera` (perfil),
  `status`, `start` / `stop` / `restart`, `logs`, `test-notify`.
- **Calibración** (`bin/ojota-tune.py`): muestra el % de cambio por frame
  en vivo para ajustar los umbrales.
- **Configuración** (`config/ojota.conf.example`): todos los parámetros
  documentados en un solo archivo; los secretos van en `config/ojota.conf`
  (no versionado).
- **Documentación**: `README.md` (arquitectura en Mermaid, setup,
  configuración, diagnóstico), `PRIVACY.md`, `LICENSE` (MIT),
  `docs/logo.svg`.
- Entorno Python aislado (`requirements.txt`: numpy).

### Estado

Etapas 1–4 completas (entorno, captura + detección, Google Drive,
integración + notificaciones). Pendientes: LaunchDaemon (Etapa 5),
retención + heartbeat externo (Etapa 6).

#!/bin/bash
# ojota-clip-hook — se dispara al cerrar cada clip.
#
#   ojota-clip-hook.sh <ruta_clip.mp4> <epoch_del_evento>
#   env: OJOTA_NOTIFY=0|1 (default 1), OJOTA_CONF
#
# 1. Sube el clip a Google Drive con reintentos.
# 2. Verifica que subió (existe y el tamaño coincide).
# 3. Solo si subió OK: notifica (respetando la ventana de silencio) y
#    borra el archivo local. Si falló, lo deja en pending/ para reintentar.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="${OJOTA_CONF:-$HERE/config/ojota.conf}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

: "${RCLONE_REMOTE:=gdrive}"
: "${RCLONE_PATH:=ojota}"
: "${NOTIFY_SILENCE_MINUTES:=5}"
: "${OJOTA_NOTIFY:=1}"
: "${INSTANT_NOTIFY_DELAY_SECONDS:=45}"

LOG="$HERE/logs/ojota-hook.log"
NOTIFY_STATE="$HERE/clips/.last_notify"
RETURN_WINDOW="$HERE/clips/.return-window"
DEST="${RCLONE_REMOTE}:${RCLONE_PATH}"
RCLONE_OPTS=(--retries 5 --retries-sleep 15s --low-level-retries 10
            --contimeout 20s --timeout 120s)

clip="${1:?falta la ruta del clip}"
event_ts="${2:-$(date +%s)}"
name="$(basename "$clip")"

mkdir -p "$HERE/logs"
log() {
    # rotación simple: si pasa 1 MB, deja un .1
    if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ]; then
        mv -f "$LOG" "$LOG.1"
    fi
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

[ -f "$clip" ] || { log "hook: no existe $clip, nada que hacer"; exit 0; }

if ! rclone listremotes 2>/dev/null | grep -qx "${RCLONE_REMOTE}:"; then
    log "hook: el remote '${RCLONE_REMOTE}:' no está configurado en rclone — abortando, $name queda en pending/"
    exit 1
fi

# lock por-archivo (atómico, portable en macOS)
lock="$clip.lockdir"
if ! mkdir "$lock" 2>/dev/null; then
    log "hook: $name ya está siendo procesado, salgo"
    exit 0
fi
trap 'rmdir "$lock" 2>/dev/null' EXIT

# ── esperar el mismo margen que el aviso instantáneo antes de decidir ──
# El pipeline (armar+subir+avisar) puede ser más rápido que la
# reconexión del celu al volver; decidir demasiado pronto deja pasar
# avisos que la ventana de "volviste" (más abajo) debería haber frenado.
if [ -n "${PHONE_IP:-}" ]; then
    target=$(( event_ts + INSTANT_NOTIFY_DELAY_SECONDS ))
    wait_s=$(( target - $(date +%s) ))
    [ "$wait_s" -gt 0 ] && sleep "$wait_s"
fi

# ── ¿ventana de "volviste"? → archivar sin notificar ────────────────
subdir=""
if [ -f "$RETURN_WINDOW" ] && [ "$(date +%s)" -lt "$(cat "$RETURN_WINDOW" 2>/dev/null || echo 0)" ]; then
    subdir="/probablemente-vos"
    OJOTA_NOTIFY=0
    log "hook: $name en ventana de 'volviste' → probablemente-vos/, sin aviso"
fi

# ── 1. subir ────────────────────────────────────────────────────────
local_size="$(stat -f%z "$clip" 2>/dev/null || echo 0)"
if ! rclone copy "${RCLONE_OPTS[@]}" "$clip" "$DEST$subdir/" 2>>"$LOG"; then
    log "hook: FALLÓ la subida de $name — queda en pending/ para reintentar"
    exit 1
fi

# ── 2. verificar ────────────────────────────────────────────────────
remote_size="$(rclone size --json "$DEST$subdir/$name" 2>/dev/null \
               | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')"
if [ -z "$remote_size" ] || [ "$remote_size" != "$local_size" ]; then
    log "hook: verificación falló de $name (local=$local_size remoto=${remote_size:-?}) — no borro"
    exit 1
fi
log "hook: subido OK $name ($local_size bytes)"

# ── 3a. frame para la notificación (antes de borrar) ────────────────
frame=""
dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$clip" 2>/dev/null)"
if [ -n "$dur" ]; then
    at="$(awk "BEGIN{d=$dur; o=d*0.6; print (o>d-1 ? (d>1?d-1:0) : o)}")"
    frame="$(mktemp -t ojota_frame).jpg"
    ffmpeg -nostdin -loglevel error -ss "$at" -i "$clip" -frames:v 1 -q:v 4 -y "$frame" 2>>"$LOG" \
        || { rm -f "$frame"; frame=""; }
fi

# ── 3b. notificar (si corresponde y pasó la ventana de silencio) ────
if [ "$OJOTA_NOTIFY" = "1" ]; then
    now="$(date +%s)"
    last="$(cat "$NOTIFY_STATE" 2>/dev/null || echo 0)"
    silence=$(( NOTIFY_SILENCE_MINUTES * 60 ))
    if [ $(( now - last )) -ge "$silence" ]; then
        link="$(rclone link "$DEST$subdir/$name" 2>>"$LOG")"
        hora="$(date -r "$event_ts" '+%H:%M' 2>/dev/null || date '+%H:%M')"
        durr="$(awk "BEGIN{printf \"%d\", $dur+0.5}" 2>/dev/null || echo '?')"
        "$HERE/bin/ojota-notify.sh" 4 "eyes,movie" "🩴 Ojo, movimiento en casa" \
            "${hora} · video de ${durr}s. Tocá para ver qué fue." "$link" "$frame" \
            && echo "$now" >"$NOTIFY_STATE"
        log "hook: notificado ($name, link $link)"
    else
        mins=$(( (silence - (now - last)) / 60 + 1 ))
        log "hook: notificación suprimida por ventana de silencio (~${mins} min) — el clip se subió igual"
    fi
else
    log "hook: perfil sin notificaciones — $name subido, sin aviso"
fi

# ── 3c. borrar local ───────────────────────────────────────────────
[ -n "$frame" ] && rm -f "$frame"
rm -f "$clip"
log "hook: local borrado $name"

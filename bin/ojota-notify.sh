#!/bin/bash
# ojota-notify — manda una notificación push por ntfy.sh
#
#   ojota-notify.sh PRIORITY TAGS TITLE MESSAGE [CLICK_URL] [ATTACH_FILE]
#
# PRIORITY: 1..5 (5 = urgente). TAGS: coma-separados (ej. "camera,warning").
# Lee NTFY_SERVER / NTFY_TOPIC de la config. Sale 0 si ntfy respondió OK.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="${OJOTA_CONF:-$HERE/config/ojota.conf}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

: "${NTFY_SERVER:=https://ntfy.sh}"
: "${NTFY_TOPIC:?falta NTFY_TOPIC en la config}"

priority="${1:-3}"
tags="${2:-}"
title="${3:-ojota}"
message="${4:-}"
click="${5:-}"
attach="${6:-}"

args=(-sS -o /dev/null -w '%{http_code}' --max-time 20
      -H "Title: ${title}"
      -H "Priority: ${priority}")
[ -n "$tags" ]  && args+=(-H "Tags: ${tags}")
[ -n "$click" ] && args+=(-H "Click: ${click}")

if [ -n "$attach" ] && [ -f "$attach" ]; then
    args+=(-H "Message: ${message}"
           -H "Filename: $(basename "$attach")"
           -T "$attach")
else
    args+=(-d "$message")
fi

code="$(curl "${args[@]}" "${NTFY_SERVER%/}/${NTFY_TOPIC}" 2>/dev/null)"
if [ "$code" = "200" ]; then
    exit 0
fi
echo "ojota-notify: ntfy respondió HTTP ${code:-sin-respuesta}" >&2
exit 1

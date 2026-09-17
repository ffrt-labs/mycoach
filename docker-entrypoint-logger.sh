#!/bin/sh
# Substitutes MYCOACH_API_ORIGIN into the shell before Caddy starts, so the
# same image works for any deployment without rebaking. Empty/unset stays
# empty — app.js then falls back to relative /api paths (same-origin dev).
set -eu

api_origin="${MYCOACH_API_ORIGIN:-}"
sed "s|\${MYCOACH_API_ORIGIN}|${api_origin}|" /srv/index.html.tmpl > /srv/index.html

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile

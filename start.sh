#!/bin/zsh
# CineID one-command launcher. Run from Terminal.app (not VSCode — it
# intercepts Cmd+Shift+X). The desktop app embeds the API server.
cd "$(dirname "$0")"
exec venv/bin/python src/desktop_app.py

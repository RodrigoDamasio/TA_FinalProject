#!/bin/bash
# SessionStart hook (see .claude/settings.json). Cloud sessions only:
# installs the backend/frontend dependencies once the code exists.
# Locally it exits at once; the user's machine has its own setup.
[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
BACKEND="$ROOT/Final_project/backend"
FRONTEND="$ROOT/Final_project/frontend"

if [ -f "$BACKEND/requirements.txt" ] && [ ! -d "$BACKEND/.venv" ]; then
  (cd "$BACKEND" && uv venv --python 3.12 -q && uv pip install -q -r requirements.txt \
    && { [ ! -f requirements-dev.txt ] || uv pip install -q -r requirements-dev.txt; }) \
    || echo "WARNING: backend dependency install failed"
fi

if [ -f "$FRONTEND/package-lock.json" ] && [ ! -d "$FRONTEND/node_modules" ] && [ -x /opt/node24/bin/npm ]; then
  (cd "$FRONTEND" && PATH="/opt/node24/bin:$PATH" npm ci --no-audit --no-fund --loglevel=error) \
    || echo "WARNING: frontend dependency install failed"
fi

exit 0

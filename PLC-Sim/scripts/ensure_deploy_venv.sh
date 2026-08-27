#!/usr/bin/env bash
# Ensure a deploy target uses Python 3.11 without replacing a working venv
# until the new environment and all dependencies are ready.
set -Eeuo pipefail

ROOT="${1:?usage: ensure_deploy_venv.sh PROJECT_ROOT}"
ROOT="$(cd "$ROOT" && pwd)"
VENV="$ROOT/.venv"
REQUIREMENTS="$ROOT/requirements.txt"

python_is_311() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' \
        >/dev/null 2>&1
}

python_version() {
    "$1" --version 2>&1 || printf 'unknown\n'
}

resolve_python() {
    local requested="${PLCSIM_PYTHON:-}"
    local candidate=""

    if [ -n "$requested" ]; then
        if [ -x "$requested" ]; then
            candidate="$requested"
        else
            candidate="$(command -v "$requested" 2>/dev/null || true)"
        fi
        if [ -z "$candidate" ]; then
            printf '[X] PLCSIM_PYTHON does not exist or is not executable: %s\n' \
                "$requested" >&2
            return 1
        fi
        if ! python_is_311 "$candidate"; then
            printf '[X] PLCSIM_PYTHON must be Python 3.11.x: %s (%s)\n' \
                "$candidate" "$(python_version "$candidate")" >&2
            return 1
        fi
        printf '%s\n' "$candidate"
        return 0
    fi

    candidate="$(command -v python3.11 2>/dev/null || true)"
    if [ -n "$candidate" ] && python_is_311 "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    printf '[X] Python 3.11 interpreter was not found on the server.\n' >&2
    printf '    Install Python 3.11 with its venv module, or set PLCSIM_PYTHON.\n' >&2
    if [ -x "$VENV/bin/python" ]; then
        printf '    Existing environment: %s\n' "$(python_version "$VENV/bin/python")" >&2
    fi
    return 1
}

install_requirements() {
    local python="$1"

    PIP_DISABLE_PIP_VERSION_CHECK=1 "$python" -m pip install -q -r "$REQUIREMENTS"
}

if [ ! -f "$REQUIREMENTS" ]; then
    printf '[X] Missing requirements file: %s\n' "$REQUIREMENTS" >&2
    exit 1
fi

if [ -x "$VENV/bin/python" ] && python_is_311 "$VENV/bin/python"; then
    printf '[OK] Reusing Python 3.11 environment: %s\n' "$VENV"
    install_requirements "$VENV/bin/python"
    exit 0
fi

BOOTSTRAP_PYTHON="$(resolve_python)"
STAGING="$(mktemp -d "$ROOT/.venv-python311-new.XXXXXX")"
BACKUP=""

cleanup_staging() {
    if [ -n "${STAGING:-}" ] && [ -d "$STAGING" ]; then
        rm -rf -- "$STAGING"
    fi
}
trap cleanup_staging EXIT

printf '[1/3] Creating staged Python 3.11 environment with %s\n' "$BOOTSTRAP_PYTHON"
if ! "$BOOTSTRAP_PYTHON" -m venv "$STAGING"; then
    printf '[X] Failed to create a venv. Install the Python 3.11 venv module.\n' >&2
    exit 1
fi
if ! python_is_311 "$STAGING/bin/python"; then
    printf '[X] Staged environment is not Python 3.11; existing .venv was preserved.\n' >&2
    exit 1
fi

printf '[2/3] Installing dependencies into the staged environment\n'
install_requirements "$STAGING/bin/python"

if [ -e "$VENV" ] || [ -L "$VENV" ]; then
    BACKUP="$ROOT/.venv-before-python311-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
    mv "$VENV" "$BACKUP"
fi

printf '[3/3] Activating Python 3.11 environment\n'
if ! mv "$STAGING" "$VENV"; then
    if [ -n "$BACKUP" ] && [ ! -e "$VENV" ]; then
        mv "$BACKUP" "$VENV"
    fi
    printf '[X] Failed to activate the staged environment; previous .venv restored.\n' >&2
    exit 1
fi
STAGING=""
trap - EXIT

printf '[OK] Active environment: %s (%s)\n' \
    "$VENV" "$(python_version "$VENV/bin/python")"
if [ -n "$BACKUP" ]; then
    printf '[OK] Previous environment retained temporarily: %s\n' "$BACKUP"
fi

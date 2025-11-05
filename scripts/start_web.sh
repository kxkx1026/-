#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[1/4] 가상환경을 생성합니다 (.venv)."
  python3 -m venv "$VENV_DIR"
  echo "[2/4] pip을 최신 버전으로 업데이트합니다."
  "$VENV_DIR/bin/pip" install --upgrade pip
  echo "[3/4] requirements.txt 의존성을 설치합니다."
  "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/requirements.txt"
else
  echo "이미 .venv 가상환경이 존재합니다. 필요 시 직접 재설치하세요."
fi

source "$VENV_DIR/bin/activate"

echo "가상환경이 활성화되었습니다."

if [ -z "${DART_API_KEY:-}" ]; then
  read -rsp "DART API 키를 입력하세요: " DART_API_KEY_INPUT
  echo
  export DART_API_KEY="$DART_API_KEY_INPUT"
fi

echo "FLASK_APP=web.app 으로 설정하고 서버를 실행합니다. (중지하려면 Ctrl+C)"
export FLASK_APP=web.app
exec flask run

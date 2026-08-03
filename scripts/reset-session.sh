#!/bin/bash
# Сброс накопленного контекста Claude-сессии. Запускается из cron.
# Безопасно: пропускается если ask() сейчас выполняется (неблокирующий lock).
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

export PATH="$HOME/.local/bin:$PATH"

uv run python -c "
from d_brain.config import get_settings
from d_brain.services.runtime import get_session
s = get_session(get_settings())
result = s.force_recover()
print(f'session reset: {\"ok\" if result else \"skipped (busy)\"}')
"

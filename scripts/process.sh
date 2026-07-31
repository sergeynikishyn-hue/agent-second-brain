#!/bin/bash
set -e

# PATH for systemd (claude, uv, npx, node)
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node/" 2>/dev/null | tail -1)/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Paths — auto-detect from script location
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VAULT_DIR="$PROJECT_DIR/vault"
ENV_FILE="$PROJECT_DIR/.env"

# Load environment variables (set -a survives quoted values and spaces,
# unlike export $(... | xargs), which word-splits and strips quotes)
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

# Check token
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "ERROR: TELEGRAM_BOT_TOKEN not set"
    exit 1
fi

# Timezone (configure in .env: TZ=Your/Timezone)
export TZ="${TZ:-UTC}"

# Date and chat_id
TODAY=$(date +%Y-%m-%d)
CHAT_ID="${ALLOWED_USER_IDS//[\[\] ]/}"  # strip brackets/spaces from [123, 456]
CHAT_ID="${CHAT_ID%%,*}"  # first id only — the admin gets the report

echo "=== d-brain processing for $TODAY ==="

# ── ORIENT PHASE: pre-flight checks ──
DAILY_FILE="$VAULT_DIR/daily/$TODAY.md"
HANDOFF_FILE="$VAULT_DIR/.session/handoff.md"
GRAPH_FILE="$VAULT_DIR/.graph/vault-graph.json"

# Check daily file exists and has content
if [ ! -f "$DAILY_FILE" ]; then
    echo "ORIENT: daily/$TODAY.md not found — creating empty file"
    echo "# $TODAY" > "$DAILY_FILE"
fi

DAILY_SIZE=$(wc -c < "$DAILY_FILE" 2>/dev/null || echo "0")
if [ "$DAILY_SIZE" -lt 50 ]; then
    echo "ORIENT: daily/$TODAY.md is empty ($DAILY_SIZE bytes) — skipping Claude processing"
    echo "ORIENT: Running graph rebuild only"

    # Still rebuild graph and commit
    cd "$VAULT_DIR"
    uv run .claude/skills/autograph/scripts/graph.py health . || echo "Graph rebuild failed (non-critical)"
    cd "$PROJECT_DIR"

    git add -A
    git commit -m "chore: process daily $TODAY" || true
    git push || true
    echo "=== Done (empty daily, graph-only) ==="
    exit 0
fi

# Check handoff exists
if [ ! -f "$HANDOFF_FILE" ]; then
    echo "ORIENT: handoff.md not found — creating stub"
    mkdir -p "$VAULT_DIR/.session"
    echo -e "---\nupdated: $(date -Iseconds)\n---\n\n## Last Session\n(none)\n\n## Observations" > "$HANDOFF_FILE"
fi

# Check graph freshness (warn if >7 days old)
if [ -f "$GRAPH_FILE" ]; then
    GRAPH_AGE=$(( ($(date +%s) - $(stat -c %Y "$GRAPH_FILE" 2>/dev/null || stat -f %m "$GRAPH_FILE" 2>/dev/null || echo 0)) / 86400 ))
    if [ "$GRAPH_AGE" -gt 7 ]; then
        echo "ORIENT: vault-graph.json is $GRAPH_AGE days old (>7)"
    fi
fi

echo "ORIENT: daily=$DAILY_SIZE bytes, handoff=OK, graph=OK"
# ── END ORIENT PHASE ──

# ── PRE-PROCESS: reset accumulated session context ──
# After days of chat and pipeline runs the tmux session accumulates up to
# 14 MB of pane history, causing immediate Anthropic rate-limiting (< 10 s)
# every time the pipeline sends its first prompt. A fresh session has zero
# context overhead and doesn't hit rate limits. Skip cleanly if a live chat
# request is holding the session lock.
echo "=== Resetting session context before processing ==="
cd "$PROJECT_DIR" && uv run python -c "
from d_brain.config import get_settings
from d_brain.services.runtime import get_session
s = get_session(get_settings())
result = s.force_recover()
print('Session reset:', 'ok — fresh session ready' if result else 'skipped (chat in progress, using existing session)')
" || echo "Session reset failed (continuing with existing session)"

# ── PROCESS via the persistent interactive session (NO claude -p) ──
# The 3-phase claude -p pipeline is gone: after 2026-06-15 that bills against
# the Agent SDK credit. We drive the long-lived interactive session instead,
# which stays on the subscription. The Python entrypoint runs the daily
# processing through the shared session and prints the HTML report.
echo "=== Daily processing (interactive session) ==="
# stderr is kept OUT of $REPORT (goes to this script's own stderr, captured
# by journald) — merging it in (2>&1) used to let Python logging noise leak
# into the text sent to Telegram.
REPORT=$(cd "$PROJECT_DIR" && uv run python -m d_brain.pipeline daily) || true

echo "=== pipeline output ==="
echo "$REPORT"
echo "======================="

# Remove HTML comments and unsupported Telegram tags (hr, div, span, etc.)
# Telegram HTML supports only: b, i, code, s, u, a, pre, blockquote, tg-spoiler
REPORT_CLEAN=$(echo "$REPORT" | sed '/<!--/,/-->/d' | sed 's|<hr[^>]*>|\n|gI' | sed 's|</\?div[^>]*>||gI' | sed 's|</\?span[^>]*>||gI')

# Rebuild vault graph (keeps structure up to date)
echo "=== Rebuilding vault graph ==="
cd "$VAULT_DIR"
uv run .claude/skills/autograph/scripts/graph.py health . || echo "Graph rebuild failed (non-critical)"

# Memory decay (update relevance scores and tiers)
echo "=== Memory decay ==="
uv run .claude/skills/autograph/scripts/engine.py decay . || echo "Memory decay failed (non-critical)"
cd "$PROJECT_DIR"

# Git commit
git add -A
git commit -m "chore: process daily $TODAY" || true
git push || true

# Send to Telegram
if [ -n "$REPORT_CLEAN" ] && [ -n "$CHAT_ID" ]; then
    echo "=== Sending to Telegram ==="
    # Success is judged by "ok":true, not the absence of "ok":false — a
    # network blip/timeout leaves $RESULT empty, which contains neither
    # substring and used to be silently treated as success.
    RESULT=$(curl -s --max-time 20 -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d "chat_id=$CHAT_ID" \
        -d "text=$REPORT_CLEAN" \
        -d "parse_mode=HTML")
    echo "Telegram response: ${RESULT:-<empty — curl failed or timed out>}"

    if ! echo "$RESULT" | grep -q '"ok":true'; then
        echo "Send failed, retrying once after 3s..."
        sleep 3
        RESULT=$(curl -s --max-time 20 -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
            -d "chat_id=$CHAT_ID" \
            -d "text=$REPORT_CLEAN" \
            -d "parse_mode=HTML")
        echo "Telegram retry response: ${RESULT:-<empty — curl failed or timed out>}"
    fi

    # If HTML still failing (e.g. malformed markup), fall back to plain text
    if ! echo "$RESULT" | grep -q '"ok":true'; then
        echo "HTML send failed: $RESULT"
        RESULT=$(curl -s --max-time 20 -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
            -d "chat_id=$CHAT_ID" \
            -d "text=$REPORT_CLEAN")
        echo "Telegram plain-text response: ${RESULT:-<empty — curl failed or timed out>}"
    fi

    if ! echo "$RESULT" | grep -q '"ok":true'; then
        echo "ERROR: Telegram delivery failed after retries: $RESULT"
    fi
fi

echo "=== Done ==="

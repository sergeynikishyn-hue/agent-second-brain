"""Tests for ClaudeProcessor after migration to ClaudeSession.

A FakeSession replaces the real tmux session; we assert the processor builds
a prompt, calls ask(), and maps AskResult into the {report|error} dict the
bot handlers already expect.
"""

from datetime import date

from d_brain.services.claude_session import AskResult
from d_brain.services.processor import ClaudeProcessor


class FakeSession:
    def __init__(self, result: AskResult | list[AskResult]) -> None:
        # A single AskResult repeats on every call; a list is consumed in
        # order (the last entry repeats once exhausted) — used to simulate
        # a transient failure followed by success/failure on retry.
        self.results = result if isinstance(result, list) else [result]
        self.prompts: list[str] = []
        self.request_ids: list[str | None] = []

    def ask(self, prompt: str, **kwargs) -> AskResult:  # noqa: ANN003
        self.prompts.append(prompt)
        self.request_ids.append(kwargs.get("request_id"))
        idx = min(len(self.prompts) - 1, len(self.results) - 1)
        return self.results[idx]


def _daily(tmp_path):
    (tmp_path / "daily").mkdir(exist_ok=True)
    (tmp_path / "daily" / "2026-06-07.md").write_text("# d\n")
    return date(2026, 6, 7)


def test_process_daily_maps_rate_limited(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("rate_limited"))
    p = ClaudeProcessor(tmp_path, session=sess)
    r = p.process_daily(day)
    assert "error" in r and r["processed_entries"] == 0


def test_process_daily_maps_logged_out(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("logged_out"))
    p = ClaudeProcessor(tmp_path, session=sess)
    r = p.process_daily(day)
    assert "error" in r


def test_process_daily_maps_timeout(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("timeout", detail="no reply"))
    p = ClaudeProcessor(tmp_path, session=sess)
    r = p.process_daily(day)
    assert "error" in r


def test_process_daily_missing_file_does_not_call_session(tmp_path):
    sess = FakeSession(AskResult("ok", reply="x"))
    p = ClaudeProcessor(tmp_path, session=sess)
    r = p.process_daily(date(2026, 6, 7))
    assert "error" in r
    assert sess.prompts == []  # never reached the session


def test_process_daily_ok(tmp_path):
    (tmp_path / "daily").mkdir()
    (tmp_path / "daily" / "2026-06-07.md").write_text(
        "# 2026-06-07\n## 10:00 [text]\nbuy milk\n"
    )
    sess = FakeSession(AskResult("ok", reply="<b>processed</b>"))
    p = ClaudeProcessor(tmp_path, session=sess)
    r = p.process_daily(date(2026, 6, 7))
    assert r["report"] == "<b>processed</b>"
    assert sess.prompts


def test_prompts_contain_no_todoist_references(tmp_path):
    """v3.0: Todoist is removed; no prompt may instruct the agent to use it."""
    (tmp_path / "daily").mkdir()
    (tmp_path / "daily" / "2026-06-07.md").write_text("# d\n")
    sess = FakeSession(AskResult("ok", reply="x"))
    p = ClaudeProcessor(tmp_path, session=sess)
    p.process_daily(date(2026, 6, 7))
    joined = "\n".join(sess.prompts).lower()
    assert "todoist" not in joined


def test_dbrain_processor_skill_tree_is_todoist_free():
    """v3.0: the daily-processing skill (embedded into the daily prompt) and
    the vault rules must not instruct any Todoist usage."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "vault" / ".claude"
    hits = []
    for sub in ("skills/dbrain-processor", "rules"):
        for f in (root / sub).rglob("*.md"):
            if "todoist" in f.read_text().lower():
                hits.append(str(f))
    assert not hits, f"todoist instructions remain in: {hits}"


def test_process_daily_prompt_instructs_cards_and_summary(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("ok", reply="x"))
    p = ClaudeProcessor(tmp_path, session=sess)
    p.process_daily(day)
    prompt = sess.prompts[0].lower()
    assert "autograph" in prompt
    assert "саммари" in prompt or "summary" in prompt


def test_daily_processing_is_tagged_as_maintenance(tmp_path):
    # The nightly pipeline shares the brain session with chat; its turns
    # carry a maint- request_id so user input is never steered into them.
    sess = FakeSession(AskResult("ok", reply='{"status": "ok"}'))
    day = _daily(tmp_path)
    ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert sess.request_ids
    assert all(r and r.startswith("maint-") for r in sess.request_ids)


def test_process_daily_retries_once_on_transient_error(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession([AskResult("error", detail="session stalled"), AskResult("ok", reply="<b>done</b>")])
    r = ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert r["report"] == "<b>done</b>"
    assert len(sess.prompts) == 2


def test_process_daily_retries_once_on_timeout(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession([AskResult("timeout", detail="no reply"), AskResult("ok", reply="<b>done</b>")])
    r = ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert r["report"] == "<b>done</b>"
    assert len(sess.prompts) == 2


def test_process_daily_gives_up_after_one_retry(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("error", detail="session stalled"))
    r = ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert "error" in r
    assert len(sess.prompts) == 2  # original + one retry, then gives up


def test_process_daily_does_not_retry_rate_limited(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("rate_limited"))
    r = ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert "error" in r
    assert len(sess.prompts) == 1  # no point retrying a real quota limit


def test_process_daily_does_not_retry_logged_out(tmp_path):
    day = _daily(tmp_path)
    sess = FakeSession(AskResult("logged_out"))
    r = ClaudeProcessor(tmp_path, session=sess).process_daily(day)
    assert "error" in r
    assert len(sess.prompts) == 1  # needs a human to run `dbrain login`

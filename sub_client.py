"""Run the Telegram bot's model calls on the Claude subscription (Claude Code `claude -p`)
instead of an Anthropic API key. Modelled on ~/GCBot/sub_client.py.

- SubscriptionClient mimics the part of the Anthropic SDK that telegram_bot.py uses:
  `client.messages.create(model=, system=, tools=, messages=)` -> object with `.stop_reason`
  and `.content` blocks (`text` / `tool_use`), so the bot's tool loop stays as it is.
- `claude -p` has no custom function calling, so tools are emulated: the tool list goes into
  the system prompt and the model must answer with a JSON object (enforced by --json-schema)
  that is either a tool request or the final reply. The bot runs the tools itself, as before.
- Images are written to a temp folder and read by the model with the Read tool.
- ANTHROPIC_* is stripped from the child environment, so nothing is billed to an API key.
- Every call is appended to logs/bot_usage.jsonl;  `python sub_client.py usage`  prints totals.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
USAGE_LOG = BASE_DIR / "logs" / "bot_usage.jsonl"
CALL_TIMEOUT = 300          # seconds for one model call
HISTORY_MAX = 30            # messages of history sent per call (the local memory file never resets)
TOOL_RESULT_CAP = 6000      # chars kept per OLD tool result in the history (the latest is sent whole)

_usage_lock = threading.Lock()

_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool", "reply"]},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "input": {"type": "object"}},
                "required": ["name", "input"],
            },
        },
        "text": {"type": "string"},
    },
    "required": ["action"],
}


class SubscriptionError(Exception):
    """A failed `claude -p` call."""


class _Block(dict):
    """A content block usable both as `block.type` (SDK style) and as a plain dict, so the
    conversation memory holding it stays JSON-serializable."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def _get(block, key, default=None):
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _claude_bin() -> str:
    for cand in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude")):
        if cand.exists():
            return str(cand)
    found = shutil.which("claude")
    if not found:
        raise SubscriptionError("Claude Code CLI not found (expected ~/.local/bin/claude)")
    return found


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("ANTHROPIC_") or k.startswith("CLAUDE"))}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    # Each call is one-shot, so a cache write (billed at a premium) would never be read back.
    env["DISABLE_PROMPT_CACHING"] = "1"
    return env


def _log_usage(model: str, data: dict) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "model": model,
           "cli_cost_usd": data.get("total_cost_usd"), "duration_ms": data.get("duration_ms"),
           "models": {}}
    for name, u in (data.get("modelUsage") or {}).items():
        rec["models"][name] = {"in": u.get("inputTokens", 0), "out": u.get("outputTokens", 0),
                               "cache_w": u.get("cacheCreationInputTokens", 0),
                               "cache_r": u.get("cacheReadInputTokens", 0)}
    try:
        with _usage_lock:
            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _tools_prompt(tools) -> str:
    lines = [
        "",
        "## Tools",
        "You cannot call functions directly. Your whole answer is one JSON object:",
        '- to use tools:  {"action": "tool", "calls": [{"name": "<tool name>", "input": {...}}]}',
        '- to answer the user:  {"action": "reply", "text": "<your message to the user>"}',
        "After a tool request you are called again with the results under [tool_result]. "
        "Never invent a tool result, and never put a tool request inside \"text\".",
        "Available tools (name, description, JSON Schema of the input):",
    ]
    for t in tools:
        lines.append(f"- {t['name']}: {t.get('description', '')}\n"
                     f"  input schema: {json.dumps(t.get('input_schema', {}), ensure_ascii=False)}")
    return "\n".join(lines)


def _is_turn_start(msg) -> bool:
    """A user message typed by the person (text or photo), not a tool_result carrier."""
    if msg.get("role") != "user":
        return False
    c = msg.get("content")
    if isinstance(c, str):
        return True
    return not any(_get(b, "type") == "tool_result" for b in c)


class _Messages:
    def create(self, *, model, messages, system=None, tools=None, **_ignored):
        workdir = Path(tempfile.mkdtemp(prefix="chrono_bot_cli_"))
        try:
            prompt, images = self._build_prompt(messages, workdir)
            sys_prompt = (system or "You are a helpful assistant.")
            if tools:
                sys_prompt += "\n" + _tools_prompt(tools)
            cmd = [_claude_bin(), "-p", "--model", model, "--output-format", "json",
                   "--restricted", "--strict-mcp-config", "--no-session-persistence",
                   "--system-prompt", sys_prompt]
            if tools:
                cmd += ["--json-schema", json.dumps(_REPLY_SCHEMA)]
            if images:
                cmd += ["--tools", "Read", "--allowedTools", "Read"]
            else:
                cmd += ["--tools", ""]
            try:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                   timeout=CALL_TIMEOUT, cwd=str(workdir), env=_child_env())
            except subprocess.TimeoutExpired:
                raise SubscriptionError(f"Claude subscription call timed out after {CALL_TIMEOUT}s") from None
            try:
                data = json.loads(r.stdout)
            except ValueError:
                tail = (r.stderr or r.stdout or "").strip()[-400:]
                raise SubscriptionError(f"Claude subscription call failed (exit {r.returncode}): {tail}") from None
            _log_usage(model, data)
            if data.get("is_error") or r.returncode != 0:
                raise SubscriptionError("Claude subscription call failed: "
                                        + str(data.get("result") or data.get("subtype") or r.stderr)[:400])
            return self._to_response(data, model, bool(tools))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _to_response(data: dict, model: str, has_tools: bool):
        text = data.get("result") or ""
        out = data.get("structured_output") if has_tools else None
        if has_tools and not isinstance(out, dict):
            try:                                   # older CLIs: the JSON arrives as the result text
                out = json.loads(text)
            except ValueError:
                out = None
        if isinstance(out, dict):
            calls = [c for c in (out.get("calls") or []) if isinstance(c, dict) and c.get("name")]
            if out.get("action") == "tool" and calls:
                blocks = [_Block(type="tool_use", id="toolu_" + uuid.uuid4().hex[:20],
                                 name=c["name"], input=c.get("input") or {}) for c in calls]
                return _Block(content=blocks, stop_reason="tool_use", model=model)
            text = out.get("text") or ""
        return _Block(content=[_Block(type="text", text=text)], stop_reason="end_turn", model=model)

    @staticmethod
    def _render(content, workdir: Path, images: list, full_results: bool) -> str:
        if isinstance(content, str):
            return content
        parts = []
        for block in content:
            kind = _get(block, "type")
            if kind == "text":
                parts.append(_get(block, "text", ""))
            elif kind == "image":
                src = _get(block, "source") or {}
                ext = {"image/png": ".png", "image/webp": ".webp",
                       "image/gif": ".gif"}.get(src.get("media_type"), ".jpg")
                p = workdir / f"photo_{len(images) + 1}{ext}"
                p.write_bytes(base64.standard_b64decode(src.get("data", "")))
                images.append(p)
                parts.append(f"[The user sent a photo. It is the file ./{p.name} in the current "
                             "directory. Look at it with the Read tool before answering.]")
            elif kind == "tool_use":
                parts.append("[tool_request] " + json.dumps(
                    {"name": _get(block, "name"), "input": _get(block, "input")}, ensure_ascii=False))
            elif kind == "tool_result":
                res = _get(block, "content", "")
                if not isinstance(res, str):
                    res = json.dumps(res, ensure_ascii=False, default=str)
                if not full_results and len(res) > TOOL_RESULT_CAP:
                    res = res[:TOOL_RESULT_CAP] + "\n…(truncated)"
                parts.append("[tool_result]\n" + res)
        return "\n\n".join(p for p in parts if p)

    def _build_prompt(self, messages, workdir: Path):
        msgs = list(messages)
        if len(msgs) > HISTORY_MAX:
            cut = len(msgs) - HISTORY_MAX
            while cut < len(msgs) - 1 and not _is_turn_start(msgs[cut]):
                cut += 1                       # never start the history in the middle of a tool round
            msgs = msgs[cut:]
        # the current turn = from the last message the person typed to the end
        start = len(msgs) - 1
        while start > 0 and not _is_turn_start(msgs[start]):
            start -= 1
        images = []
        rendered = [(m["role"], self._render(m["content"], workdir, images, i >= start))
                    for i, m in enumerate(msgs)]
        if len(rendered) == 1:
            return rendered[0][1], images
        history = "\n\n".join(f"[{role}]\n{text}" for role, text in rendered[:start])
        current = "\n\n".join(f"[{role}]\n{text}" for role, text in rendered[start:])
        prompt = ""
        if history:
            prompt += "<conversation_so_far>\n" + history + "\n</conversation_so_far>\n\n"
        prompt += ("<current_turn>\n" + current + "\n</current_turn>\n\n"
                   "Continue the current turn: answer the user's latest message, using the tool "
                   "results already shown in it and the conversation so far as context.")
        return prompt, images


class SubscriptionClient:
    def __init__(self):
        self.messages = _Messages()


# ---------------------------------------------------------------------------------- usage report
def usage_report() -> str:
    if not USAGE_LOG.exists():
        return "no usage recorded yet"
    months = {}
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        m = months.setdefault(rec["ts"][:7], {"calls": 0, "cli": 0.0, "in": 0, "out": 0})
        m["calls"] += 1
        m["cli"] += rec.get("cli_cost_usd") or 0
        for u in rec.get("models", {}).values():
            m["in"] += u["in"] + u["cache_w"] + u["cache_r"]
            m["out"] += u["out"]
    lines = ["month     calls   input tok   output tok   list-price value (not billed)"]
    for k in sorted(months):
        v = months[k]
        lines.append(f"{k}   {v['calls']:5d}   {v['in']:9d}   {v['out']:10d}   ${v['cli']:8.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "usage"
    if what == "usage":
        print(usage_report())
    elif what == "selftest":
        model = sys.argv[2] if len(sys.argv) > 2 else "claude-opus-4-8"
        resp = SubscriptionClient().messages.create(
            model=model, messages=[{"role": "user", "content": "Reply with exactly: OK"}])
        print("selftest reply:", resp.content[0].text.strip())

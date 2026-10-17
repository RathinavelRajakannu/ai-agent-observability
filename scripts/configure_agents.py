#!/usr/bin/env python3
"""Points every installed AI coding agent's OpenTelemetry export at the local stack.

Idempotent: re-running skips agents that are already configured. Every file it
touches is backed up to ~/.ai-observability-backups/<timestamp>/ first.

    python3 scripts/configure_agents.py             # apply
    python3 scripts/configure_agents.py --dry-run   # show what would change
    python3 scripts/configure_agents.py --no-content  # metrics/events only: no prompts, commands or tool args

Agents: Claude Code, Codex CLI, Grok Build, Gemini CLI, Qwen Code.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home()
HTTP = "http://localhost:4318"
GRPC = "http://localhost:4317"
MARK = "# --- ai-agent-observability ---"


def backup(path: Path, dest: Path):
    if path.exists():
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / f"{path.parent.name.lstrip('.')}-{path.name}")


def claude(content: bool):
    path = HOME / ".claude" / "settings.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    env = data.setdefault("env", {})
    want = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",  # traces (spans per interaction / model call / tool)
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": HTTP,
        "OTEL_METRIC_EXPORT_INTERVAL": "10000",
        "OTEL_LOGS_EXPORT_INTERVAL": "5000",
        "OTEL_LOG_TOOL_DETAILS": "1" if content else "0",
        "OTEL_LOG_USER_PROMPTS": "1" if content else "0",
    }
    if all(env.get(k) == v for k, v in want.items()):
        return path, None
    env.update(want)
    return path, json.dumps(data, indent=2) + "\n"


def toml_append(path: Path, table: str, body: str):
    """Appends a TOML table unless it (or our marker) already exists."""
    text = path.read_text() if path.exists() else ""
    if MARK in text:
        return path, None
    if any(line.strip() == f"[{table}]" for line in text.splitlines()):
        print(f"  ! {path} already has a [{table}] table - add these keys to it by hand:\n{body}")
        return path, ""
    return path, text.rstrip("\n") + f"\n\n{MARK}\n[{table}]\n{body}"


def codex(content: bool):
    return toml_append(HOME / ".codex" / "config.toml", "otel", "\n".join([
        'environment = "local"',
        f"log_user_prompt = {'true' if content else 'false'}",
        f'exporter = {{ otlp-http = {{ endpoint = "{HTTP}/v1/logs", protocol = "binary" }} }}',
        f'metrics_exporter = {{ otlp-http = {{ endpoint = "{HTTP}/v1/metrics", protocol = "binary" }} }}',
        f'trace_exporter = {{ otlp-http = {{ endpoint = "{HTTP}/v1/traces", protocol = "binary" }} }}',
    ]) + "\n")


def grok(content: bool):
    flag = "true" if content else "false"
    return toml_append(HOME / ".grok" / "config.toml", "telemetry", "\n".join([
        "otel_enabled = true",
        'otel_metrics_exporter = "otlp"',
        'otel_logs_exporter = "otlp"',
        f'otel_endpoint = "{HTTP}"',
        'otel_protocol = "http/protobuf"',
        f"otel_log_user_prompts = {flag}",
        f"otel_log_tool_details = {flag}",
    ]) + "\n")


def gemini_like(dirname: str):
    def configure(content: bool):
        path = HOME / dirname / "settings.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        want = {"enabled": True, "target": "local", "otlpEndpoint": GRPC, "otlpProtocol": "grpc", "logPrompts": content}
        if data.get("telemetry") == want:
            return path, None
        data["telemetry"] = want
        return path, json.dumps(data, indent=2) + "\n"
    return configure


AGENTS = {
    "Claude Code": (".claude", claude),
    "Codex CLI": (".codex", codex),
    "Grok Build": (".grok", grok),
    "Gemini CLI": (".gemini", gemini_like(".gemini")),
    "Qwen Code": (".qwen", gemini_like(".qwen")),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-content", action="store_true", help="don't export prompts, commands or tool arguments")
    args = ap.parse_args()

    dest = HOME / ".ai-observability-backups" / time.strftime("%Y%m%d-%H%M%S")
    for name, (dirname, fn) in AGENTS.items():
        if not (HOME / dirname).exists():
            print(f"- {name}: not installed (~/{dirname} missing), skipped")
            continue
        path, new = fn(not args.no_content)
        if new is None:
            print(f"= {name}: already configured ({path})")
            continue
        if new == "":
            print(f"! {name}: needs the manual edit above")
            continue
        if args.dry_run:
            print(f"~ {name}: would update {path}")
            continue
        backup(path, dest)
        path.write_text(new)
        print(f"+ {name}: updated {path}")
    if not args.dry_run:
        print(f"\nBackups (if any): {dest}\nRestart running agent sessions to pick up the change.")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generates grafana/dashboards/ai-agents.json.

Every panel reads the agent-neutral ai_* attributes that the collector's
transform/normalize processor stamps onto log events (see otel/otelcol-config.yaml),
so a new agent only needs a collector rule, not dashboard changes.

    python3 scripts/build_dashboard.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grafana" / "dashboards" / "ai-agents.json"

LOKI = {"type": "loki", "uid": "loki"}
PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}

# Fixed color per agent (categorical slots, dark-surface steps) - color follows the agent.
AGENTS = {
    "claude-code": "#3987e5",
    "codex": "#d95926",
    "gemini-cli": "#199e70",
    "grok-build": "#c98500",
    "qwen-code": "#d55181",
}

SEL = '{service_name=~".+"} | agent_name=~"$agent" | ai_session_id=~"${session}.*"'
TOKENS = f'{SEL} | ai_tokens_total!=""'
TS = '| label_format ts=`{{ unixEpochMillis __timestamp__ }}` | unwrap ts'
TOOL_EVENTS = 'ai_event=~"tool_result|tool_call"'


def agent_overrides():
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in AGENTS.items()
    ]


_next_id = 0


def panel(kind, title, grid, targets, description="", **extra):
    global _next_id
    _next_id += 1
    p = {
        "id": _next_id,
        "type": kind,
        "title": title,
        "description": description,
        "gridPos": dict(zip("xywh", grid)),
        "datasource": targets[0].get("datasource", LOKI) if targets else LOKI,
        "targets": targets,
    }
    p.update(extra)
    return p


def loki(expr, ref="A", instant=False, legend="", fmt=None):
    t = {"refId": ref, "datasource": LOKI, "expr": expr, "queryType": "instant" if instant else "range"}
    if legend:
        t["legendFormat"] = legend
    if fmt:
        t["format"] = fmt
    return t


def stat(title, grid, expr, unit="short", description="", decimals=None):
    defaults = {"unit": unit, "color": {"mode": "fixed", "fixedColor": "text"}, "noValue": "0"}
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        "stat", title, grid, [loki(expr, instant=True)], description,
        fieldConfig={"defaults": defaults, "overrides": []},
        options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                 "colorMode": "none", "graphMode": "none", "textMode": "value", "justifyMode": "center"},
    )


def timeseries(title, grid, targets, unit="short", description="", stack=True, bars=True):
    return panel(
        "timeseries", title, grid, targets, description,
        interval="1m",
        fieldConfig={
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars" if bars else "line",
                    "lineWidth": 2, "fillOpacity": 85 if bars else 0, "barAlignment": 0,
                    "stacking": {"mode": "normal" if stack else "none"},
                    "showPoints": "always", "pointSize": 6, "spanNulls": False,
                    "axisSoftMin": 0,
                },
            },
            "overrides": agent_overrides(),
        },
        options={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                 "tooltip": {"mode": "multi", "sort": "desc"}},
    )


def build():
    panels = []

    # ---- Overview -------------------------------------------------------------
    panels.append({"type": "row", "title": "Overview (selected time range)", "collapsed": False,
                   "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}, "id": 100, "panels": []})
    y = 1
    stats = [
        ("Sessions", 4, f'count(sum by (ai_session_id) (count_over_time({SEL} | ai_session_id!="" [$__range])))', "short",
         "Distinct agent sessions (Codex: conversations) that emitted any event."),
        ("Tokens", 4, f'sum(sum_over_time({TOKENS} | unwrap ai_tokens_total [$__range]))', "short",
         "All tokens processed: input + output (incl. reasoning) + cache reads + cache writes."),
        ("Model calls", 3, f'sum(count_over_time({TOKENS} [$__range]))', "short",
         "LLM API responses that reported token usage."),
        ("Prompts", 3, f'sum(count_over_time({SEL} | ai_event="user_prompt" [$__range]))', "short", "User prompts submitted."),
        ("Tool calls", 3, f'sum(count_over_time({SEL} | {TOOL_EVENTS} [$__range]))', "short",
         "Tool executions (shell, file edits, MCP, ...)."),
        ("Agent wall time", 4,
         f'sum(max_over_time({SEL} | ai_session_id!="" {TS} [$__range]) by (ai_session_id) - min_over_time({SEL} | ai_session_id!="" {TS} [$__range]) by (ai_session_id))',
         "ms", "Sum over sessions of (last event − first event)."),
        ("Cost (USD, Claude)", 3, f'sum(sum_over_time({SEL} | ai_cost_usd!="" | unwrap ai_cost_usd [$__range]))', "currencyUSD",
         "Only Claude Code reports cost; other agents report tokens only."),
    ]
    x = 0
    for title, w, expr, unit, desc in stats:
        panels.append(stat(title, (x, y, w, 4), expr, unit, desc, decimals=2 if unit == "currencyUSD" else None))
        x += w
    y += 4

    # ---- Usage over time ------------------------------------------------------
    panels.append(timeseries(
        "Tokens by agent", (0, y, 12, 8),
        [loki(f'sum by (agent_name) (sum_over_time({TOKENS} | unwrap ai_tokens_total [$__interval]))', legend="{{agent_name}}")],
        description="Tokens processed per interval, stacked by agent."))
    by_type = [
        loki(f'sum(sum_over_time({TOKENS} | unwrap ai_tokens_{t} [$__interval]))', ref=chr(65 + i), legend=label)
        for i, (t, label) in enumerate([("input", "input (uncached)"), ("output", "output + reasoning"),
                                        ("cache_read", "cache read"), ("cache_write", "cache write")])
    ]
    panels.append(timeseries("Tokens by type", (12, y, 12, 8), by_type,
                             description="Normalized across agents; input excludes cache hits."))
    y += 8

    # ---- Sessions table -------------------------------------------------------
    by = "by (agent_name, ai_session_id)"
    sess_targets = [
        loki(f'min_over_time({SEL} | ai_session_id!="" {TS} [$__range]) {by}', "A", True, fmt="table"),
        loki(f'max_over_time({SEL} | ai_session_id!="" {TS} [$__range]) {by} - min_over_time({SEL} | ai_session_id!="" {TS} [$__range]) {by}', "B", True, fmt="table"),
        loki(f'sum {by} (count_over_time({SEL} | ai_event="user_prompt" [$__range]))', "C", True, fmt="table"),
        loki(f'sum {by} (count_over_time({SEL} | {TOOL_EVENTS} [$__range]))', "D", True, fmt="table"),
        loki(f'sum {by} (count_over_time({TOKENS} [$__range]))', "E", True, fmt="table"),
        loki(f'sum {by} (sum_over_time({TOKENS} | unwrap ai_tokens_total [$__range]))', "F", True, fmt="table"),
        loki(f'sum {by} (sum_over_time({SEL} | ai_cost_usd!="" | unwrap ai_cost_usd [$__range]))', "G", True, fmt="table"),
    ]
    rename = {"Value #A": "Started", "Value #B": "Duration", "Value #C": "Prompts", "Value #D": "Tool calls",
              "Value #E": "Model calls", "Value #F": "Tokens", "Value #G": "Cost (USD)",
              "agent_name": "Agent", "ai_session_id": "Session"}
    panels.append(panel(
        "table", "Sessions", (0, y, 24, 9), sess_targets,
        "One row per session. Click a session id to filter the whole dashboard to it.",
        transformations=[
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": rename,
                                           "indexByName": {"agent_name": 0, "ai_session_id": 1, "Value #A": 2, "Value #B": 3,
                                                           "Value #C": 4, "Value #D": 5, "Value #E": 6, "Value #F": 7, "Value #G": 8}}},
            {"id": "sortBy", "options": {"sort": [{"field": "Started", "desc": True}]}},
        ],
        fieldConfig={"defaults": {"custom": {"align": "auto"}, "noValue": "–"}, "overrides": [
            {"matcher": {"id": "byName", "options": "Started"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}]},
            {"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "ms"}]},
            {"matcher": {"id": "byName", "options": "Tokens"}, "properties": [
                {"id": "unit", "value": "short"},
                {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                {"id": "color", "value": {"mode": "fixed", "fixedColor": "#3987e5"}}]},
            {"matcher": {"id": "byName", "options": "Cost (USD)"}, "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 3}]},
            {"matcher": {"id": "byName", "options": "Session"}, "properties": [{"id": "custom.width", "value": 285}, {"id": "links", "value": [
                {"title": "Filter dashboard to this session", "url": "/d/ai-agents/ai-agents-observability?${__url_time_range}&var-agent=${agent}&var-session=${__value.raw}"}]}]},
        ]},
        options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
    ))
    y += 9

    # ---- Live activity --------------------------------------------------------
    feed = (
        f'{SEL} | ai_event=~"user_prompt|tool_result|tool_call|tool_decision|api_request|api_response|api_error|'
        f'assistant_response|session_start|session_end|conversation_starts|subagent|compaction|sse_event"'
        ' | ai_event!~"sse_event|api_request|api_response" or ai_tokens_total!=""'
        ' | line_format `{{ alignLeft 11 .agent_name }} │ {{ alignLeft 8 (trunc 8 .ai_session_id) }} │ {{ alignLeft 18 .ai_event }} │ '
        '{{ if .ai_command }}$ {{ .ai_command }}{{ if .decision }}  [{{ .decision }}]{{ end }}{{ if .duration_ms }}  ({{ .duration_ms }} ms){{ end }}'
        '{{ else if .ai_tool }}{{ .ai_tool }} {{ trunc 240 .ai_detail }}{{ if .decision }}  [{{ .decision }}]{{ end }}'
        '{{ else if .ai_prompt }}» {{ trunc 400 .ai_prompt }}'
        '{{ else if .ai_tokens_total }}{{ .model }} · {{ .ai_tokens_total }} tokens{{ if .duration_ms }} · {{ .duration_ms }} ms{{ end }}{{ if .ai_cost_usd }} · ${{ .ai_cost_usd }}{{ end }}'
        '{{ else if .response }}« {{ trunc 300 .response }}'
        '{{ else }}{{ .model }}{{ end }}`'
    )
    panels.append(panel(
        "logs", "Live activity — what the agents are doing", (0, y, 24, 13), [loki(feed)],
        "Prompts, tool calls / shell commands, model calls and responses from every agent, newest first. "
        "Columns: agent │ session │ event │ detail.",
        options={"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                 "dedupStrategy": "none", "prettifyLogMessage": False, "showCommonLabels": False, "showLabels": False},
    ))
    y += 13

    # ---- Commands + tools -----------------------------------------------------
    panels.append(panel(
        "table", "Shell commands executed", (0, y, 15, 10),
        [loki(f'{SEL} | ai_event=~"tool_result|tool_call" | ai_command!="" | line_format `{{{{ .ai_command }}}}`')],
        "Every shell command an agent ran (Grok Build never exports command text by design).",
        transformations=[
            {"id": "extractFields", "options": {"source": "labels", "format": "auto", "replace": False, "keepTime": False}},
            {"id": "organize", "options": {
                "includeByName": {"Time": True, "agent_name": True, "ai_session_id": True, "Line": True, "success": True, "duration_ms": True},
                "indexByName": {"Time": 0, "agent_name": 1, "Line": 2, "success": 3, "duration_ms": 4, "ai_session_id": 5},
                "renameByName": {"agent_name": "Agent", "Line": "Command", "success": "OK", "duration_ms": "ms", "ai_session_id": "Session"}}},
        ],
        fieldConfig={"defaults": {"noValue": "–"}, "overrides": [
            {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "custom.width", "value": 170}]},
            {"matcher": {"id": "byName", "options": "Agent"}, "properties": [{"id": "custom.width", "value": 110}]},
            {"matcher": {"id": "byName", "options": "OK"}, "properties": [{"id": "custom.width", "value": 60}]},
            {"matcher": {"id": "byName", "options": "ms"}, "properties": [{"id": "custom.width", "value": 70}]},
            {"matcher": {"id": "byName", "options": "Session"}, "properties": [{"id": "custom.width", "value": 300}]},
        ]},
        options={"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Time", "desc": True}]},
    ))
    panels.append(panel(
        "barchart", "Tool usage", (15, y, 9, 10),
        [loki(f'sum by (agent_name, ai_tool) (count_over_time({SEL} | {TOOL_EVENTS} [$__range]))', instant=True, fmt="table")],
        "Tool calls in the selected range, per agent and tool.",
        transformations=[
            {"id": "groupingToMatrix", "options": {"columnField": "agent_name", "rowField": "ai_tool", "valueField": "Value #A", "emptyValue": "zero"}},
        ],
        fieldConfig={"defaults": {"unit": "short", "color": {"mode": "palette-classic"},
                                  "custom": {"fillOpacity": 90, "lineWidth": 0}}, "overrides": agent_overrides()},
        options={"orientation": "horizontal", "stacking": "normal", "showValue": "never", "barRadius": 0.1,
                 "groupWidth": 0.7, "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "multi"}, "xTickLabelMaxLength": 24},
    ))
    y += 10

    # ---- Per-agent comparison -------------------------------------------------
    panels.append(panel(
        "bargauge", "Tokens per agent", (0, y, 8, 8),
        [loki(f'sum by (agent_name) (sum_over_time({TOKENS} | unwrap ai_tokens_total [$__range]))', instant=True, fmt="table")],
        transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True}}}],
        fieldConfig={"defaults": {"unit": "short", "min": 0, "displayName": "${__field.labels.agent_name}",
                                  "color": {"mode": "fixed", "fixedColor": "#3987e5"}}, "overrides": []},
        options={"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "text",
                 "namePlacement": "top", "text": {"titleSize": 13, "valueSize": 16}, "minVizHeight": 18, "maxVizHeight": 28, "reduceOptions": {"calcs": ["lastNotNull"], "values": True, "fields": "/^Value/"}},
    ))
    panels.append(timeseries(
        "Model call latency (avg)", (8, y, 8, 8),
        [loki(f'avg by (agent_name) (avg_over_time({TOKENS} | duration_ms!="" | unwrap duration_ms [$__interval]))', legend="{{agent_name}}")],
        unit="ms", stack=False, bars=False,
        description="Average duration of model calls that report latency (Claude Code, Grok Build, Gemini CLI)."))
    panels.append(timeseries(
        "Tool calls over time", (16, y, 8, 8),
        [loki(f'sum by (agent_name) (count_over_time({SEL} | {TOOL_EVENTS} [$__interval]))', legend="{{agent_name}}")]))
    y += 8

    # ---- Traces ---------------------------------------------------------------
    panels.append(panel(
        "table", "Recent traces (Claude Code & Codex emit spans)", (0, y, 24, 8),
        [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 30, "tableType": "traces",
          "query": '{resource.agent_name=~"${agent:regex}"}'}],
        "Click a trace id to open the span waterfall: interaction → model call → tool execution.",
        datasource=TEMPO,
    ))

    return {
        "uid": "ai-agents",
        "title": "AI Agents Observability",
        "description": "Sessions, tokens, time and live activity across Claude Code, Codex, Grok Build, Gemini CLI and Qwen Code.",
        "tags": ["ai", "agents", "opentelemetry"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now"},
        "templating": {"list": [
            {"name": "agent", "label": "Agent", "type": "custom", "multi": True, "includeAll": True, "allValue": ".+",
             "query": ",".join(AGENTS), "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
             "options": []},
            {"name": "session", "label": "Session id (prefix)", "type": "textbox", "query": "",
             "current": {"text": "", "value": ""}},
        ]},
        "annotations": {"list": []},
        "links": [{"title": "Explore logs", "type": "link", "url": "/explore", "icon": "compass"}],
        "panels": panels,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT}")

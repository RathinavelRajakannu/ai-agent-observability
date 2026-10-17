# ai-agent-observability

Local OpenTelemetry stack + Grafana dashboard monitoring all AI coding agents
(Claude Code, Codex, Grok Build, Gemini CLI, Qwen Code). Published at
github.com/RathinavelRajakannu/ai-agent-observability. See README.md for usage.

## Layout
- `docker-compose.yml` — single `grafana/otel-lgtm` container, ports on 127.0.0.1 only
  (Grafana 3300 because 3000 is used by another local project).
- `otel/otelcol-config.yaml` — replaces the image's collector config. The
  `transform/normalize` processor is the heart: it maps each agent's schema to `ai_*` attributes.
- `scripts/build_dashboard.py` → generates `grafana/dashboards/ai-agents.json`. Edit the
  generator, never the JSON by hand. Grafana reloads the file within ~10s.
- `scripts/check_panels.py` — runs every panel query via the Grafana API; use after any change.
- `scripts/configure_agents.py` — writes per-agent telemetry config (idempotent, backs up
  to `~/.ai-observability-backups/`).

## Gotchas
- Dashboard token/session numbers come from Loki events (`ai_tokens_*`), not Prometheus
  counters: Codex metrics have no session id and omit its prewarm request.
- If you do query Prometheus counters, use `increase(m[$__range] anchored)`; plain
  `increase()` extrapolates (≈30% high on short sessions).
- Loki turns dotted attribute names into underscores (`session.id` → `session_id`); OTTL
  in the collector uses the original dotted names.
- The collector's own logs are off by default; set `ENABLE_LOGS_OTELCOL: "true"` in the
  compose env to debug OTTL.

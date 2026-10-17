#!/usr/bin/env python3
"""Runs every query of the provisioned dashboard through Grafana and reports row counts.

    python3 scripts/check_panels.py [--from now-24h]
"""
import json, sys, urllib.request

GRAFANA = "http://localhost:3300"
frm = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "now-24h"

def get(path):
    return json.load(urllib.request.urlopen(GRAFANA + path))

dash = get("/api/dashboards/uid/ai-agents")["dashboard"]
failed = 0
for p in dash["panels"]:
    if p["type"] == "row":
        continue
    for t in p["targets"]:
        q = dict(t)
        for k in ("expr", "query"):
            if k in q:
                q[k] = (q[k].replace("${agent:regex}", ".+").replace("$agent", ".+")
                             .replace("${session}", "").replace("${__url_time_range}", ""))
        body = json.dumps({"queries": [q], "from": frm, "to": "now"}).encode()
        req = urllib.request.Request(GRAFANA + "/api/ds/query", body, {"Content-Type": "application/json"})
        try:
            res = json.load(urllib.request.urlopen(req))["results"][t["refId"]]
        except urllib.error.HTTPError as e:
            res = json.load(e)["results"][t["refId"]] if e.code == 400 else {"error": str(e)}
        if res.get("error"):
            failed += 1
            print(f"FAIL  {p['title']} [{t['refId']}]: {res['error'][:300]}")
            continue
        frames = res.get("frames", [])
        rows = sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in frames)
        sample = ""
        if frames and frames[0]["data"]["values"]:
            vals = frames[0]["data"]["values"]
            sample = str([v[-1] if v else None for v in vals])[:140]
        print(f"{'ok  ' if rows else 'EMPTY'}  {p['title']} [{t['refId']}]: {len(frames)} frames, {rows} rows {sample}")
sys.exit(1 if failed else 0)

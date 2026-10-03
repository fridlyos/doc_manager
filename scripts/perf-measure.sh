#!/usr/bin/env bash
# Lightweight search/ask latency harness (Phase 8.h). NOT part of the runtime
# image — a dev tool for a documented local baseline, not a CI gate.
#
# Measures the reliably-scriptable metrics against an already-indexed corpus:
# search P50/P95 over N queries, and one Ask (retrieval + generation) timing.
# Scan/index throughput is read separately from the jobs API (see
# docs/operations/performance.md).
#
#   API=http://127.0.0.1:8000 N=30 QUERY="renewal clause" ./scripts/perf-measure.sh
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
N="${N:-30}"
QUERY="${QUERY:-the renewal clause covers december terms}"
PROVIDER="${PROVIDER:-ollama}"
TOP_K="${TOP_K:-10}"

command -v curl >/dev/null || { echo "curl required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }

echo "== doc_manager perf: $API (N=$N, top_k=$TOP_K) =="

# Warm up (model/collection load) — excluded from the sample.
curl -s -o /dev/null -X POST "$API/api/v1/search" \
  -H 'content-type: application/json' \
  -d "{\"query\":$(python3 -c 'import json,os;print(json.dumps(os.environ["QUERY"]))'),\"retrieval\":{\"top_k\":$TOP_K}}" || true

echo "-- search latency (ms) --"
: > /tmp/docman-search-ms
for _ in $(seq "$N"); do
  ms=$(curl -s -o /dev/null -w '%{time_total}' -X POST "$API/api/v1/search" \
    -H 'content-type: application/json' \
    -d "{\"query\":$(python3 -c 'import json,os;print(json.dumps(os.environ["QUERY"]))'),\"retrieval\":{\"top_k\":$TOP_K}}")
  python3 -c "print($ms*1000)" >> /tmp/docman-search-ms
done
python3 - <<'PY'
import statistics as s
xs = sorted(float(x) for x in open("/tmp/docman-search-ms"))
n = len(xs)
p = lambda q: xs[min(n-1, int(q*n))]
print(f"  n={n} p50={p(0.50):.1f} p95={p(0.95):.1f} max={xs[-1]:.1f} mean={s.mean(xs):.1f}")
PY

echo "-- ask (retrieval + generation) --"
ask_ms=$(curl -s -o /tmp/docman-ask.json -w '%{time_total}' -X POST "$API/api/v1/ask" \
  -H 'content-type: application/json' \
  -d "{\"question\":$(python3 -c 'import json,os;print(json.dumps(os.environ["QUERY"]))'),\"provider_id\":\"$PROVIDER\"}") || true
python3 -c "print(f'  total={$ask_ms*1000:.0f} ms')" 2>/dev/null || true
python3 - <<'PY' 2>/dev/null || true
import json
d = json.load(open("/tmp/docman-ask.json"))
t = d.get("timing", {})
print(f"  server retrieval={t.get('retrieval_ms')} ms generation={t.get('generation_ms')} ms total={t.get('total_ms')} ms")
print(f"  provider={d.get('provider',{}).get('provider_id')} status={d.get('status')} citations={len(d.get('citations',[]))}")
PY

echo "done. Record results in docs/operations/performance.md with corpus + hardware."

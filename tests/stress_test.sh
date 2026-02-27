#!/usr/bin/env bash
# AgentPod concurrent stress test
# Usage: bash stress_test.sh <api_key> [max_concurrency] [step] [start_from]
#
# Ramps up concurrent requests from start_from to max_concurrency.
# Each level sends concurrent requests, waits for all to finish,
# then reports success/failure counts before moving to the next level.

set -euo pipefail

API_KEY="${1:?Usage: bash stress_test.sh <api_key> [max_concurrency] [step] [start_from]}"
MAX_CONC="${2:-30}"
STEP="${3:-5}"
START="${4:-$STEP}"
HOST="http://localhost:8000"

# Short prompts that trigger tool calls (cheap on tokens)
PROMPTS=(
  '{"content": "run: echo hello"}'
  '{"content": "list files in current dir"}'
  '{"content": "run: date"}'
  '{"content": "run: uname -a"}'
  '{"content": "run: whoami"}'
  '{"content": "run: pwd"}'
  '{"content": "run: ls /tmp"}'
  '{"content": "run: echo test123"}'
)

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "=== AgentPod Stress Test ==="
echo "Host: $HOST"
echo "Range: $START -> $MAX_CONC (step: $STEP)"
echo "Temp dir: $TMPDIR"
echo ""

# Health check first
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HOST/v1/health")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FATAL: health check failed (HTTP $HTTP_CODE). Is the server running?"
  exit 1
fi
echo "Health check: OK"
echo ""

send_request() {
  local id=$1
  local prompt=${PROMPTS[$((RANDOM % ${#PROMPTS[@]}))]}
  local outfile="$TMPDIR/resp_${id}"
  local start_ts=$(date +%s%N)

  HTTP_CODE=$(curl -s -N -o "$outfile" -w "%{http_code}" \
    --max-time 120 \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY" \
    -d "$prompt" \
    "$HOST/v1/query" 2>/dev/null || echo "000")

  local end_ts=$(date +%s%N)
  local duration_ms=$(( (end_ts - start_ts) / 1000000 ))

  if [ "$HTTP_CODE" = "200" ] && grep -q "event: done" "$outfile" 2>/dev/null; then
    echo "  [#${id}] OK  ${duration_ms}ms"
    echo "OK" > "$TMPDIR/status_${id}"
  else
    echo "  [#${id}] FAIL HTTP=$HTTP_CODE ${duration_ms}ms"
    echo "FAIL:$HTTP_CODE" > "$TMPDIR/status_${id}"
  fi
}

total_ok=0
total_fail=0
level=0

for conc in $(seq "$START" "$STEP" "$MAX_CONC"); do
  level=$((level + 1))
  echo "--- Level $level: $conc concurrent requests ---"

  # Clean previous status files
  rm -f "$TMPDIR"/status_*

  # Launch $conc requests in parallel
  pids=()
  for i in $(seq 1 "$conc"); do
    send_request "${level}_${i}" &
    pids+=($!)
  done

  # Wait for all
  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done

  # Count results
  ok=$(grep -rl "^OK$" "$TMPDIR"/status_* 2>/dev/null | wc -l || echo 0)
  fail=$((conc - ok))
  total_ok=$((total_ok + ok))
  total_fail=$((total_fail + fail))

  echo "  Result: $ok/$conc succeeded, $fail failed"
  echo ""

  if [ "$fail" -gt 0 ]; then
    echo "=== FAILURES DETECTED at concurrency=$conc ==="
    echo "Last stable concurrency: $((conc - STEP))"
    echo ""
    # Continue to see how bad it gets, or break
    if [ "$fail" -ge "$conc" ]; then
      echo "=== ALL REQUESTS FAILED. Stopping. ==="
      break
    fi
  fi

  # Brief pause between levels
  sleep 2
done

echo "=== Summary ==="
echo "Total OK:   $total_ok"
echo "Total FAIL: $total_fail"
echo "Max concurrency tested: $conc"

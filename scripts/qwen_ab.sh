#!/bin/zsh
# Qwen3.6-27B A/B on repetition 1 (30 cases): linear, then ToT. Local MLX server only;
# never touches the Claude key. Finished cases are skipped, so re-running resumes.
# The MLX server is restarted before every case, with a capped prompt cache: a
# long-lived server exceeds Metal's buffer limit ("[metal::malloc] Resource limit").
cd "${0:A:h}/.."
unset ANTHROPIC_API_KEY IC_ANTHROPIC_API_KEY
export IC_REQUEST_TIMEOUT=3600 IC_MAX_RETRIES=0   # no duplicate requests against the local server
PY=.venv/bin/python
SERVER_PID=""
start_server() {
  [ -n "$SERVER_PID" ] && { kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; }
  pkill -f 'mlx_lm.server' 2>/dev/null; sleep 2
  .venv/bin/mlx_lm.server --model mlx-community/Qwen3.6-27B-4bit --port 8080 \
    --prompt-cache-size 2 >> evals/archive/mlx_server.log 2>&1 &
  SERVER_PID=$!
  for i in {1..120}; do curl -s -m 3 http://localhost:8080/v1/models >/dev/null && return 0; sleep 5; done
  echo "server never came up"; exit 1
}
run_mode() {  # $1 = results folder, $2.. = extra eval flags
  local out=$1; shift
  for c in $($PY -c "from incident_commander import loaders; print(' '.join(x.case_id for x in loaders.list_cases('online-boutique') if x.run == '1'))"); do
    [ -f "evals/results/$out/${c//\//_}.json" ] && continue
    start_server
    $PY -m incident_commander.cli eval --model env --runs 1 --cases "$c" --out "$out" "$@"
  done
  $PY -m incident_commander.cli eval --model env --runs 1 --out "$out" "$@"   # all saved: rescores, writes summary
}
# Folders can be overridden; LINEAR=skip re-uses an existing linear run (ToT-only code changes).
LINEAR=${LINEAR:-2026-11-06_qwen3.6-27b_r1_v4_linear}
TOT=${TOT:-2026-11-06_qwen3.6-27b_r1_v4_tot-model}
[ "$LINEAR" != skip ] && run_mode $LINEAR
run_mode $TOT --tot model
[ -n "$SERVER_PID" ] && kill $SERVER_PID 2>/dev/null
echo ALL_DONE

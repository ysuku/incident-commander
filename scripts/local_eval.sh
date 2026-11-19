#!/bin/zsh
# Local-only eval runner: one results folder, any eval flags, $0. Generalises
# qwen_ab.sh (kept as the record of the v4 A/B).
#
#   scripts/local_eval.sh <out_folder> [eval flags...]
#   scripts/local_eval.sh 2026-11-18_qwen_r1_graph        --arch graph --tot model
#   scripts/local_eval.sh 2026-11-18_qwen_r1_hide-traces  --tot model --hide traces
#   scripts/local_eval.sh 2026-11-18_qwen_re3             --tot model --system online-boutique-re3
#
# - Never reaches a paid API: the Claude keys are unset, --model is refused, and
#   the OpenAI-compatible client is pointed at the local MLX server whatever .env says.
# - Resumable: a case with a saved brief is skipped, so re-running continues.
# - The MLX server is restarted before every case, with a capped prompt cache: a
#   long-lived server exceeds Metal's buffer limit ("[metal::malloc] Resource limit").
# - Stops if src/ changes mid-run, so one folder is one version of the code
#   (written to code_fingerprint.txt; a folder from other code is refused).
# - Held-out system: every call here is one case, so the one-shot stamp
#   (heldout_complete.json) is written only when ALL cases at --runs are saved;
#   a --cases subset is a part of the run and never stamps it.
# Repetition 1 only unless --runs is given. MLX_MODEL / MLX_PORT override the server.
cd "${0:A:h}/.."
[ $# -ge 1 ] && [[ "$1" != -* ]] || { echo "usage: scripts/local_eval.sh <out_folder> [eval flags...]"; exit 64; }
OUT=$1; shift

SYSTEM=online-boutique; RUNS=1; GLOB='*'; FLAGS=()
while [ $# -gt 0 ]; do
  case $1 in
    --system) SYSTEM=$2; FLAGS+=($1 $2); shift 2 ;;
    --runs)   RUNS=$2; shift 2 ;;      # passed per case below
    --cases)  GLOB=$2; shift 2 ;;
    --model|--out|--max-usd|--limit)
      echo "$1 is not accepted here: this runner is local-only, one folder, every case"; exit 64 ;;
    *) FLAGS+=($1); shift ;;
  esac
done

unset ANTHROPIC_API_KEY IC_ANTHROPIC_API_KEY
MLX_MODEL=${MLX_MODEL:-mlx-community/Qwen3.6-27B-4bit}
MLX_PORT=${MLX_PORT:-8080}
# exported values win over .env (model.py uses setdefault), so no remote endpoint or key is used
export OPENAI_BASE_URL="http://localhost:$MLX_PORT/v1" OPENAI_API_KEY=local OPENAI_MODEL=$MLX_MODEL
export IC_REQUEST_TIMEOUT=3600 IC_MAX_RETRIES=0   # no duplicate requests against the local server
PY=.venv/bin/python
DIR="evals/results/$OUT"
SERVER_PID=""

code_hash() { find src -name '*.py' -print0 | sort -z | xargs -0 shasum | shasum | cut -c1-12; }
CODE=$(code_hash); echo "code fingerprint $CODE"
mkdir -p "$DIR"
if [ -f "$DIR/code_fingerprint.txt" ] && [ "$(cat "$DIR/code_fingerprint.txt")" != "$CODE" ]; then
  echo "CODE_MISMATCH: $DIR was started with code $(cat "$DIR/code_fingerprint.txt"), src/ is now $CODE; use a new folder"
  exit 2
fi
echo "$CODE" > "$DIR/code_fingerprint.txt"

stop_server() { [ -n "$SERVER_PID" ] && { kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; }; SERVER_PID=""; }
trap stop_server EXIT
trap 'exit 130' INT TERM
start_server() {
  stop_server
  pkill -f 'mlx_lm.server' 2>/dev/null; sleep 2
  .venv/bin/mlx_lm.server --model "$MLX_MODEL" --port "$MLX_PORT" \
    --prompt-cache-size 2 >> "$DIR/mlx_server.log" 2>&1 &
  SERVER_PID=$!
  for i in {1..120}; do curl -s -m 3 "http://localhost:$MLX_PORT/v1/models" >/dev/null && return 0; sleep 5; done
  echo "server never came up"; exit 1
}

# the case list, filtered as `cli eval --runs --cases` filters it (train-ticket
# needs IC_ALLOW_HELDOUT=1 from the caller: this script never sets it)
CASES=$($PY -c "
import sys
from fnmatch import fnmatch
from incident_commander import loaders
system, runs, glob = sys.argv[1:4]
print(' '.join(c.case_id for c in loaders.list_cases(system)
               if c.run in runs.split(',') and fnmatch(c.case_id, glob)))" "$SYSTEM" "$RUNS" "$GLOB") || exit 1
[ -n "$CASES" ] || { echo "no cases match --system $SYSTEM --runs $RUNS --cases $GLOB"; exit 1; }
echo "$(echo $CASES | wc -w | tr -d ' ') cases -> $DIR | flags: $FLAGS"

for c in ${=CASES}; do
  f="$DIR/${c//\//_}.json"   # a saved crash record (status "error") is not a result: run it again
  [ -f "$f" ] && ! grep -q '^  "status": "error"' "$f" && continue
  [ "$(code_hash)" != "$CODE" ] && { echo "CODE_CHANGED: src/ edited mid-run; stopping"; exit 2; }
  start_server
  $PY -m incident_commander.cli eval --model env --runs "$RUNS" --cases "$c" --out "$OUT" $FLAGS || exit 1
done
stop_server
# every case is saved: this pass calls no model, it only rescores and writes the summary
$PY -m incident_commander.cli eval --model env --runs "$RUNS" --cases "$GLOB" --out "$OUT" --saved-only $FLAGS || exit 1
echo ALL_DONE

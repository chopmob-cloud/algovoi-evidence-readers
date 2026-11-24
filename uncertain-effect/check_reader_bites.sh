#!/usr/bin/env bash
# Prove the AlgoVoi reader's checks are load-bearing.
#
# A green run over a corpus means nothing unless the reader's answer can change
# when one of its checks stops working. For each check the corpus names, this
# disables exactly that predicate in a copy of the reader, reruns the corpus
# through the external-verifier contract, and requires the cases that expect
# that reason to turn into disagreements.
#
# Exits non-zero if any mutation leaves the corpus green, or if the untouched
# reader is not green first.
set -uo pipefail

COMMIT="${COMMIT:-6ba08cbe9d7f2819e3c47c84adee2fa65390e123}"
HARNESS="${HARNESS:-agent-evidence-vectors==0.37.1}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"
READER="$HERE/algovoi_uncertain_effect.py"

mkdir -p "$WORK"
cd "$WORK" || exit 1
if [ ! -d "corpus-$COMMIT" ]; then
  URL="https://codeload.github.com/probityai/agent-evidence-vectors/tar.gz/$COMMIT"
  if command -v curl >/dev/null 2>&1; then
    curl -sSfL "$URL" -o aev.tgz
  else
    $PYTHON -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$URL" aev.tgz
  fi
  tar xzf aev.tgz && mv "agent-evidence-vectors-$COMMIT" "corpus-$COMMIT" && rm aev.tgz
fi
# The corpus moved under corpora/ on 2026-11-01; the old layout is still read so
# an earlier commit can be replayed with the same script.
if [ -d "$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect" ]; then
  CORPUS="$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect"
else
  CORPUS="$WORK/corpus-$COMMIT/vectors-uncertain-effect"
fi
# A POSIX venv keeps its entry points in bin/, a Windows one in Scripts/. Resolve
# that AFTER the venv exists: on a clean tree neither directory is there yet, and
# resolving first picks Scripts on Linux and then calls a pip that was never made.
venv_bin() {
  if [ -x "$WORK/venv/bin/python" ] || [ -x "$WORK/venv/bin/python3" ]; then
    printf '%s' "$WORK/venv/bin"
  else
    printf '%s' "$WORK/venv/Scripts"
  fi
}
if [ ! -x "$WORK/venv/bin/agent-evidence-vectors" ] \
   && [ ! -x "$WORK/venv/Scripts/agent-evidence-vectors" ] \
   && [ ! -x "$WORK/venv/bin/agent-evidence-vectors.exe" ] \
   && [ ! -x "$WORK/venv/Scripts/agent-evidence-vectors.exe" ]; then
  $PYTHON -m venv "$WORK/venv"
  BIN="$(venv_bin)"
  "$BIN/python" -m pip install -q --upgrade pip
  "$BIN/python" -m pip install -q "$HARNESS"
fi
BIN="$(venv_bin)"
RAIL="$BIN/agent-evidence-vectors"
[ -x "$RAIL" ] || RAIL="$BIN/agent-evidence-vectors.exe"
PY="$BIN/python"
[ -x "$PY" ] || PY="$BIN/python.exe"
if [ ! -x "$RAIL" ]; then
  echo "FAIL: harness not installed at $RAIL; nothing was validated"
  exit 1
fi

# A verifier command is a string the harness hands to a child process. On MSYS
# the /c/... form bash prints is not translated for a native interpreter, and a
# child that cannot open the reader reports every case as a disagreement, which
# reads as a failing reader rather than a broken harness call. Hand the child the
# Windows form of both pieces when the host is Windows.
NATIVE_PATHS=0
VERIFIER_PY="$PYTHON"
case "$(uname -s)" in
  MSYS*|MINGW*|CYGWIN*)
    if command -v cygpath >/dev/null 2>&1; then
      NATIVE_PATHS=1
      VERIFIER_PY="$(cygpath -m "$PY")"
    fi
    ;;
esac

run_reader() {  # $1 = reader path ; $2 = log path ; prints exit code
  if [ "$NATIVE_PATHS" -eq 1 ]; then
    "$RAIL" --vectors "$CORPUS" --verifier "$VERIFIER_PY $(cygpath -m "$1")" >"$2" 2>&1
  else
    "$RAIL" --vectors "$CORPUS" --verifier "$PYTHON $1" >"$2" 2>&1
  fi
  echo $?
}

printf '== untouched reader must be green ==\n'
rc="$(run_reader "$READER" baseline.log)"
tail -1 baseline.log
if [ "$rc" -ne 0 ]; then
  echo "FAIL: the unmutated reader already disagrees; mutations prove nothing"
  exit 1
fi

MUT="$WORK/mutations"
rm -rf "$MUT"; mkdir -p "$MUT"
failed=0

# reason -> ids expecting it, read straight out of the corpus manifest.
# Only deny reasons name a check; the allow reason is the pass marker.
$PYTHON - "$CORPUS/MANIFEST.json" > "$WORK/expected.tsv" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for entry in manifest["vectors"]:
    if entry["expected"]["decision"] == "deny":
        print(entry["expected"]["reason"], entry["id"])
PY

# The matrix derives its list from the corpus's deny reasons, so a check with no
# deny case would never be disabled and the run would still report green. Compare
# the two sets here and fail when any check named by the reader has no case.
READER_FOR_PY="$READER"
[ "$NATIVE_PATHS" -eq 1 ] && READER_FOR_PY="$(cygpath -m "$READER")"
$PYTHON - "$READER_FOR_PY" "$WORK/expected.tsv" <<'COVERAGE' || { echo "FAIL: a check has no deny case, so the matrix cannot exercise it"; failed=1; }
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("algovoi_uncertain_effect", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit(f"could not load the reader from {sys.argv[1]}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

checks = set(module.FIRST_FAILING)
if not checks:
    print("the reader names no checks, so this coverage proves nothing")
    raise SystemExit(1)
reasons = {line.split()[0] for line in open(sys.argv[2], encoding="utf-8") if line.strip()}
missing = checks - reasons
if missing:
    print("checks with no deny case: " + ", ".join(sorted(missing)))
    raise SystemExit(1)
print(f"coverage: {len(checks)} checks, {len(reasons)} deny reasons, none uncovered")
COVERAGE

while read -r reason; do
  [ -z "$reason" ] && continue
  mutant="$MUT/$reason.py"
  sed "s/^    \"$reason\": .*/    \"$reason\": lambda trail: False,/" "$READER" > "$mutant"
  if cmp -s "$READER" "$mutant"; then
    echo "FAIL: could not disable $reason in the reader (sed matched nothing)"
    failed=1
    continue
  fi
  rc="$(run_reader "$mutant" "$MUT/$reason.log")"
  ids="$(awk -v r="$reason" '$1==r {print $2}' "$WORK/expected.tsv")"
  expected_n="$(printf '%s\n' "$ids" | grep -c .)"
  seen=0
  missed=""
  for id in $ids; do
    if grep -q "^$id:" "$MUT/$reason.log"; then seen=$((seen + 1)); else missed="$missed $id"; fi
  done
  if [ "$rc" -eq 0 ] || [ "$seen" -lt "$expected_n" ]; then
    echo "MISS  disable $reason: exit $rc, $seen/$expected_n expected cases reported${missed:+ (missed:$missed)}"
    failed=1
  else
    echo "bites disable $reason: exit $rc, $seen/$expected_n expected cases reported"
  fi
done < <(cut -d' ' -f1 "$WORK/expected.tsv" | sort -u)

# A record whose bytes changed must be judged on its bytes: one effect in the
# trail becomes two, and the reader must name the check that catches it.
CORRUPT="$WORK/corrupt-cases"; rm -rf "$CORRUPT"; cp -r "$CORPUS/cases/allow-confirmed-first-attempt" "$CORRUPT"
if [ ! -f "$CORRUPT/record.json" ]; then
  # The corpus renames cases as it grows; take any allow case that has one
  # attempt, so the edited record is two effects against one attempt.
  for candidate in "$CORPUS"/cases/allow-*; do
    if [ -f "$candidate/record.json" ] \
       && $PYTHON -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if len(r.get('attempts') or [])==1 else 1)" "$candidate/record.json"; then
      rm -rf "$CORRUPT"; cp -r "$candidate" "$CORRUPT"; break
    fi
  done
fi
$PYTHON - "$CORRUPT/record.json" <<'PY'
import json, sys
path = sys.argv[1]
record = json.load(open(path, encoding="utf-8"))
record["ownerEffects"] = 2
open(path, "w", encoding="utf-8").write(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY
READER_NATIVE="$READER"
[ "$NATIVE_PATHS" -eq 1 ] && READER_NATIVE="$(cygpath -m "$READER")"
answer="$("$PY" "$READER_NATIVE" "$CORRUPT/record.json" --json)"; rc=$?
echo "corrupt record: exit $rc $answer"
# The corpus orders the account check before the duplicate check, so one attempt
# beside two effects is owner_effect_unaccounted: the ledger holds an effect the
# trail cannot account for, and naming it duplicate_effect would skip a check.
if [ "$rc" -eq 1 ] && printf '%s' "$answer" | grep -q '"owner_effect_unaccounted"'; then
  echo "bites edited record: reader judged the edited bytes"
else
  echo "MISS  edited record: expected deny owner_effect_unaccounted, got exit $rc $answer"
  failed=1
fi

# The reader must not consult the answer file. Refuse any reference to it.
if grep -nE 'MANIFEST\.json|expected\.json|corpusDigest' "$READER"; then
  echo "FAIL: the reader mentions the answer files"
  failed=1
else
  echo "bites no answer lookup: the reader source names neither MANIFEST.json nor expected.json"
fi

exit "$failed"

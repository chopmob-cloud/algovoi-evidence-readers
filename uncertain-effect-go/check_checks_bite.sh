#!/usr/bin/env bash
# Prove the Go reader's checks are load-bearing.
#
# A green run over a corpus means nothing unless the reader's answer can change
# when one of its checks stops working. For each reason the reader names, this
# disables exactly that predicate in a copy of the source, rebuilds, reruns the
# corpus through the external-verifier contract, and requires the cases that
# expect that reason to turn into disagreements.
#
# Exits non-zero if any mutation leaves the corpus green, if a mutation could not
# be applied, or if the untouched reader is not green first.
set -uo pipefail

COMMIT="${COMMIT:-6ba08cbe9d7f2819e3c47c84adee2fa65390e123}"
GO_VERSION="${GO_VERSION:-1.22.2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"

mkdir -p "$WORK"
cd "$WORK" || exit 1

if ! command -v go >/dev/null 2>&1; then
  if [ ! -x /usr/local/go/bin/go ]; then
    TMPTGZ="$WORK/go.tar.gz"
    URL="https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz"
    if command -v curl >/dev/null 2>&1; then
      curl -sSfL "$URL" -o "$TMPTGZ"
    else
      python3 -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" "$URL" "$TMPTGZ"
    fi
    tar -C /usr/local -xzf "$TMPTGZ"
    rm -f "$TMPTGZ"
  fi
  PATH="/usr/local/go/bin:$PATH"
  export PATH
fi

if [ -d "$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect" ]; then
  CORPUS="$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect"
else
  CORPUS="$WORK/corpus-$COMMIT/vectors-uncertain-effect"
fi
if [ ! -d "$CORPUS" ]; then
  echo "FAIL: no corpus at $CORPUS; run run.sh first"
  exit 1
fi

BIN="$WORK/venv/bin"
[ -x "$BIN/python" ] || BIN="$WORK/venv/Scripts"
RAIL="$BIN/agent-evidence-vectors"
[ -x "$RAIL" ] || RAIL="$BIN/agent-evidence-vectors.exe"
if [ ! -x "$RAIL" ]; then
  echo "FAIL: harness not installed at $RAIL; run run.sh first"
  exit 1
fi

run_reader() {  # $1 = binary, $2 = log; prints exit code
  "$RAIL" --vectors "$CORPUS" --verifier "$1" > "$2" 2>&1
  echo $?
}

printf '== untouched reader must be green ==\n'
rc="$(run_reader "$WORK/ue-go" baseline.log)"
tail -1 baseline.log
if [ "$rc" -ne 0 ]; then
  echo "FAIL: the unmutated reader already disagrees; mutations prove nothing"
  exit 1
fi

# reason -> case ids expecting it, read from the corpus manifest.
python3 - "$CORPUS/MANIFEST.json" > "$WORK/expected.tsv" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for entry in manifest["vectors"]:
    if entry["expected"]["decision"] == "deny":
        print(entry["expected"]["reason"], entry["id"])
PY

# Every reason the reader names must have a deny case, or the matrix below would
# quietly skip it and still report green.
python3 - "$HERE/main.go" "$WORK/expected.tsv" <<'PY' || { echo "FAIL: a check has no deny case, so the matrix cannot exercise it"; exit 1; }
import re, sys
source = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"var firstFailing = \[\]string\{(.*?)\n\}", source, re.S)
if block is None:
    raise SystemExit("could not find firstFailing in the reader")
checks = set(re.findall(r'"([a-z_]+)"', block.group(1)))
if not checks:
    raise SystemExit("the reader names no checks, so this coverage proves nothing")
reasons = {line.split()[0] for line in open(sys.argv[2], encoding="utf-8") if line.strip()}
missing = checks - reasons
if missing:
    print("checks with no deny case: " + ", ".join(sorted(missing)))
    raise SystemExit(1)
print(f"coverage: {len(checks)} checks, {len(reasons)} deny reasons, none uncovered")
PY

MUT="$WORK/mutants"
rm -rf "$MUT"
mkdir -p "$MUT"
cp "$HERE/main.go" "$HERE/go.mod" "$MUT/"
failed=0

while read -r reason; do
  [ -z "$reason" ] && continue
  python3 - "$MUT/main.go" "$HERE/main.go" "$reason" <<'PY' || { echo "FAIL: could not disable $reason"; failed=1; continue; }
import re, sys
mutant_path, original_path, reason = sys.argv[1], sys.argv[2], sys.argv[3]
original = open(original_path, encoding="utf-8").read()
pattern = re.compile(r'(\n\s*"%s":\s*)[A-Za-z]+(,\n)' % re.escape(reason))
mutated, count = pattern.subn(r'\1func(*trail) bool { return false }\2', original)
if count != 1:
    print(f"anchor matched {count} times")
    raise SystemExit(1)
open(mutant_path, "w", encoding="utf-8").write(mutated)
PY
  ( cd "$MUT" && go build -o ue-go-mutant . ) || { echo "FAIL: mutant for $reason does not build"; failed=1; continue; }

  rc="$(run_reader "$MUT/ue-go-mutant" "$MUT/$reason.log")"
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

# A record whose bytes changed must be judged on its bytes.
CORRUPT="$WORK/corrupt-case"
rm -rf "$CORRUPT"
mkdir -p "$CORRUPT"
CASE="$CORPUS/cases/allow-confirmed-first-attempt/record.json"
if [ ! -f "$CASE" ]; then
  CASE="$(ls "$CORPUS"/cases/allow-*/record.json 2>/dev/null | head -1)"
fi
if [ -z "${CASE:-}" ] || [ ! -f "$CASE" ]; then
  echo "FAIL: no allow case to edit"
  failed=1
else
  python3 - "$CASE" "$CORRUPT/record.json" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
record["ownerEffects"] = 2
json.dump(record, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  answer="$("$WORK/ue-go" "$CORRUPT/record.json" --json)"; rc=$?
  echo "corrupt record: exit $rc $answer"
  if [ "$rc" -eq 1 ] && printf '%s' "$answer" | grep -q '"owner_effect_unaccounted"'; then
    echo "bites edited record: the reader judged the edited bytes"
  else
    echo "MISS  edited record: expected deny owner_effect_unaccounted, got exit $rc $answer"
    failed=1
  fi
fi

# The reader must not consult the answer file.
if grep -nE 'MANIFEST\.json|expected\.json|corpusDigest' "$HERE/main.go"; then
  echo "FAIL: the reader mentions the answer files"
  failed=1
else
  echo "bites no answer lookup: the reader source names neither MANIFEST.json nor expected.json"
fi

exit "$failed"

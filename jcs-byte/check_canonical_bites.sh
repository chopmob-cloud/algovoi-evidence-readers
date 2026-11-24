#!/usr/bin/env bash
# Prove this canonicaliser's rules are load bearing.
#
# A green corpus run means nothing unless an answer can change when one rule
# stops working. Each gate below disables exactly one rule in a copy of the
# reader and requires a named case to change its verdict.
#
# Five gates run the whole corpus under the jcs profile, because the family
# they break is reached before the drift family. Three gates are targeted at a
# single case under the bounded profile, because the corpus driver runs the
# drift family under every profile and a non-integer drift case cannot be both
# refused and printed. That conflict is recorded in RUN-2026-11-24.md and is the
# reason the last three gates do not go through the whole corpus.
#
# Exits non-zero if the untouched reader is not green first, or if any mutation
# leaves its case green.
set -uo pipefail

COMMIT="${COMMIT:-227645518b00a2d44ac4c943cc49dcba1ceacae9}"
PYTHON="${PYTHON:-python3}"
NODE="${NODE:-node}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"
READER="$HERE/algovoi_jcs_canonical.py"
CORPUS="$WORK/corpus-$COMMIT/corpora/jcs-byte-vectors"
MUT="$WORK/mutations"
failed=0

[ -f "$CORPUS/cases.json" ] || { echo "FAIL: no corpus at $CORPUS; run run.sh first"; exit 1; }
command -v "$NODE" >/dev/null 2>&1 || { echo "FAIL: node is required"; exit 1; }

PY="$PYTHON"; RD="$READER"
case "$(uname -s)" in
  MSYS*|MINGW*|CYGWIN*)
    command -v cygpath >/dev/null 2>&1 && { PY="$(cygpath -m "$(command -v "$PYTHON")")"; RD="$(cygpath -m "$READER")"; } ;;
esac
native() { case "$(uname -s)" in MSYS*|MINGW*|CYGWIN*) cygpath -m "$1" ;; *) printf '%s' "$1" ;; esac; }

# Extract one case's raw input so a gate can feed it to the reader directly.
case_input() {  # $1 = family, $2 = id or first, $3 = out file
  "$PYTHON" - "$(native "$CORPUS/cases.json")" "$1" "$2" "$(native "$3")" <<'PY'
import json, sys
cases, family, which, out = json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[2], sys.argv[3], sys.argv[4]
entries = cases[family]
if which != "first":
    entries = [e for e in entries if e["id"] == which]
if not entries:
    raise SystemExit(f"no case {which} in {family}")
entry = entries[0]
raw = bytes.fromhex(entry["inputHex"]) if "inputHex" in entry else entry["input"].encode("utf-8")
open(out, "wb").write(raw)
PY
}

mutate2() {  # two replacements in one copy: $1/$2 then $3/$4, output file $5
  "$PYTHON" - "$RD" "$(native "$5")" "$1" "$2" "$3" "$4" <<'PY'
import sys
src, dst, old1, new1, old2, new2 = sys.argv[1:7]
text = open(src, encoding="utf-8").read()
for old in (old1, old2):
    if old not in text:
        raise SystemExit("mutation target not found: " + old[:60])
open(dst, "w", encoding="utf-8").write(text.replace(old1, new1).replace(old2, new2))
PY
}

mutate() {  # $1 old, $2 new, $3 out file ; fails loudly if the target is absent
  "$PYTHON" - "$RD" "$(native "$3")" "$1" "$2" <<'PY'
import sys
src, dst, old, new = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
if old not in text:
    raise SystemExit("mutation target not found: " + old[:60])
open(dst, "w", encoding="utf-8").write(text.replace(old, new))
PY
}

echo "== untouched reader must be green first =="
out="$("$NODE" "$CORPUS/check.mjs" --verifier "$PY" "$RD" 2>&1)"; rc=$?
printf '%s\n' "$out" | tail -1
if [ "$rc" -ne 0 ]; then
  echo "FAIL: the unmutated reader disagrees; mutations prove nothing"
  exit 1
fi

rm -rf "$MUT"; mkdir -p "$MUT"

corpus_gate() {  # $1 name, $2 old, $3 new, $4 expected case id
  if ! mutate "$2" "$3" "$MUT/$1.py"; then echo "FAIL: $1 could not be applied"; failed=1; return; fi
  cmp -s "$READER" "$MUT/$1.py" && { echo "FAIL: $1 left the reader unchanged"; failed=1; return; }
  out="$("$NODE" "$CORPUS/check.mjs" --verifier "$PY" "$(native "$MUT/$1.py")" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qE "^Error: $4:"; then
    echo "bites $1: with the rule disabled the corpus reports $4"
  else
    echo "MISS  $1: exit $rc, expected $4"; printf '%s\n' "$out" | sed -n '1,2p'; failed=1
  fi
}

target_gate() {  # $1 name, $2 old, $3 new, $4 family, $5 case id, $6 expected rc after mutation
  if ! mutate "$2" "$3" "$MUT/$1.py"; then echo "FAIL: $1 could not be applied"; failed=1; return; fi
  input="$MUT/$1.input"; case_input "$4" "$5" "$input" || { echo "FAIL: $1 case extraction"; failed=1; return; }
  "$PYTHON" "$READER" --profile bounded <"$input" >/dev/null 2>&1; before=$?
  "$PYTHON" "$MUT/$1.py" --profile bounded <"$input" >/dev/null 2>&1; after=$?
  if [ "$before" -ne 0 ] && [ "$after" -eq "$6" ]; then
    echo "bites $1: $5 goes from refused to exit $after when the rule is disabled"
  else
    echo "MISS  $1: $5 exit $before unmutated, $after mutated (wanted refusal then $6)"; failed=1
  fi
}

echo
echo "== corpus-level gates, jcs profile =="
corpus_gate utf16-key-order 'for key in sorted(v, key=_key_order):' 'for key in sorted(v):' utf16-nested-key-order
corpus_gate es6-number-form '        return v.text' '        return repr(v.value)' exponent-switch
corpus_gate literal-non-ascii '            parts.append(ch)' '            parts.append("\\u%04x" % cp)' ordinary-unicode
corpus_gate duplicate-keys '            raise Refused("duplicate key")' '            pass' duplicate-member
# The lone-surrogate refusal is enforced twice: by the explicit check and by the
# UTF-8 encode. Disable both in one copy, or the gate cannot bite.
lone_surrogate_gate() {
  if ! mutate2 '            raise Refused("lone surrogate")' '            pass' \
               'return _ser(value, bounded, 0).encode("utf-8")' \
               'return _ser(value, bounded, 0).encode("utf-8", "surrogatepass")' "$MUT/lone-surrogates.py"; then
    echo "FAIL: lone-surrogates could not be applied"; failed=1; return
  fi
  out="$("$NODE" "$CORPUS/check.mjs" --verifier "$PY" "$(native "$MUT/lone-surrogates.py")" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qE '^Error: lone-surrogate:'; then
    echo "bites lone-surrogates: with the check and the encoder both disabled the corpus reports lone-surrogate"
  else
    echo "MISS  lone-surrogates: exit $rc"; printf '%s\n' "$out" | sed -n '1,2p'; failed=1
  fi
}
lone_surrogate_gate

echo
echo "== targeted gates, bounded profile =="
target_gate bounded-integers '    if isinstance(v, Num):
        if bounded:' '    if isinstance(v, Num):
        if False:' canonicalCases exponent-switch 0
target_gate depth-cap '        if depth + 1 > MAX_DEPTH:' '        if False:' depthCases depth-array-129 0
target_gate noncharacters '        if bounded and _is_noncharacter(cp):' '        if False:' noncharacterCases first 0

echo
if [ "$failed" -ne 0 ]; then echo "FAILED: at least one rule is not load bearing"; exit 1; fi
echo "all eight gates bite"
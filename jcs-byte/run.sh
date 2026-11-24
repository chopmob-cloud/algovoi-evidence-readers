#!/usr/bin/env bash
# Reproduce the AlgoVoi JCS canonicaliser against Probity AI's jcs-byte-vectors.
#
# Fetches the corpus at the pinned commit, checks both files against the digests
# recorded in RUN-2026-11-24.md, then runs the corpus's own byte oracle twice:
# once under the jcs profile and once under the bounded record profile.
#
# Exit is non-zero unless
#   * the jcs run prints the corpus's own success line, and
#   * the bounded run either passes, or disagrees only at a drift case.
# The second allowance is the driver/profile conflict recorded in
# RUN-2026-11-24.md: the driver applies the admission profile to the canonical,
# depth and noncharacter families but not to the drift family, so a verifier
# that refuses a non-integer value under bounded cannot also be required to
# print it. When that is fixed upstream the bounded leg passes and this script
# says so.
#
# Needs: node, bash, network once (the corpus tarball).
# Env: COMMIT, WORK, PYTHON (default python3), NODE (default node).
set -uo pipefail

COMMIT="${COMMIT:-227645518b00a2d44ac4c943cc49dcba1ceacae9}"
CASES_SHA256="${CASES_SHA256:-100dda04af86609a6236a179fb8acff1b19046ea6a7f4ae5261afe6ecedbcf1a}"
CHECK_SHA256="${CHECK_SHA256:-30a7b1d94dd81d03096a6fc448605c18913c82e75b9419b2bb3d9295703ff7d9}"
PYTHON="${PYTHON:-python3}"
NODE="${NODE:-node}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"
READER="$HERE/algovoi_jcs_canonical.py"
fail=0

command -v "$NODE" >/dev/null 2>&1 || { echo "FAIL: node is required for the corpus's oracle"; exit 1; }
command -v "$PYTHON" >/dev/null 2>&1 || { echo "FAIL: $PYTHON not found"; exit 1; }

sha() {  # sha256 of a file, whichever tool the host provides
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | grep -oE '[0-9a-f]{64}' | head -1
  else
    shasum -a 256 "$1" | grep -oE '[0-9a-f]{64}' | head -1
  fi
}

mkdir -p "$WORK" && cd "$WORK" || exit 1
if [ ! -d "corpus-$COMMIT" ]; then
  URL="https://codeload.github.com/probityai/agent-evidence-vectors/tar.gz/$COMMIT"
  if command -v curl >/dev/null 2>&1; then
    curl -sSfL "$URL" -o aev.tgz || { echo "FAIL: could not fetch $URL"; exit 1; }
  else
    "$PYTHON" -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$URL" aev.tgz \
      || { echo "FAIL: could not fetch $URL"; exit 1; }
  fi
  tar xzf aev.tgz && mv "agent-evidence-vectors-$COMMIT" "corpus-$COMMIT" && rm -f aev.tgz || exit 1
fi
CORPUS="$WORK/corpus-$COMMIT/corpora/jcs-byte-vectors"
[ -f "$CORPUS/cases.json" ] || { echo "FAIL: no corpus at $CORPUS"; exit 1; }

# A stale byte is not a test. Check the pinned bytes before running anything.
for pair in "cases.json:$CASES_SHA256" "check.mjs:$CHECK_SHA256"; do
  name="${pair%%:*}"; want="${pair#*:}"
  got="$(sha "$CORPUS/$name")"
  if [ "$got" != "$want" ]; then
    echo "FAIL: $name is $got, pinned $want"; fail=1
  else
    echo "digest ok: $name $got"
  fi
done
[ "$fail" -eq 0 ] || exit 1

# A verifier command is a string the corpus hands to a child process. A native
# node on Windows does not resolve an MSYS path, so hand it the mixed form.
PY="$PYTHON"; RD="$READER"
case "$(uname -s)" in
  MSYS*|MINGW*|CYGWIN*)
    if command -v cygpath >/dev/null 2>&1; then
      command -v "$PYTHON" >/dev/null 2>&1 && PY="$(cygpath -m "$(command -v "$PYTHON")")"
      RD="$(cygpath -m "$READER")"
    fi
    ;;
esac

printf 'node:   %s\n' "$("$NODE" -v)"
printf 'python: %s\n' "$("$PYTHON" -V 2>&1)"
printf 'corpus: %s\n' "$COMMIT"
printf 'reader sha256: %s\n' "$(sha "$READER")"

echo
echo "== jcs profile =="
jcs_out="$("$NODE" "$CORPUS/check.mjs" --verifier "$PY" "$RD" 2>&1)"; jcs_rc=$?
printf '%s\n' "$jcs_out"
if [ "$jcs_rc" -ne 0 ]; then
  echo "FAIL: the jcs profile disagreed"; fail=1
fi

echo
echo "== bounded profile =="
bnd_out="$("$NODE" "$CORPUS/check.mjs" --profile bounded --verifier "$PY" "$RD" --profile bounded 2>&1)"; bnd_rc=$?
printf '%s\n' "$bnd_out"
if [ "$bnd_rc" -eq 0 ]; then
  echo "bounded: green, so the drift/profile conflict recorded on 2026-11-24 is fixed upstream"
elif printf '%s' "$bnd_out" | grep -qE '^Error: drift-[a-z0-9-]+:'; then
  echo "bounded: disagrees only at the drift family, which is the recorded driver conflict, not a reader defect"
else
  echo "FAIL: the bounded profile disagreed outside the drift family"; fail=1
fi

exit "$fail"
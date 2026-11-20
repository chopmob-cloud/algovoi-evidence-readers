#!/usr/bin/env bash
# Clean-room run of readers/request-effect against the pinned #377 contract.
#
# Stages the contract from the forge at the commit that last touched it, checks the
# manifest digests BEFORE running anything (a stale byte is not a test), then runs
# the reader, the bite matrix and the differential canonicaliser. Exit is non-zero
# if any stage fails, including a stage that could not run.
#
# Env: WORK (default /work), PYTHON (default python3), SITE_COMMIT.
set -o pipefail

SITE_COMMIT="${SITE_COMMIT:-808041f552278a9708bd4903860e28fe07ef18d8}"
WORK="${WORK:-/work}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The interpreter is a native binary on Windows and does not translate an
# MSYS path, so the script paths it is handed are converted first.
NATIVE=0
case "$(uname -s)" in
  MSYS*|MINGW*|CYGWIN*) command -v cygpath >/dev/null 2>&1 && NATIVE=1 ;;
esac
HERE_ARG="$HERE"
[ "$NATIVE" -eq 1 ] && HERE_ARG="$(cygpath -m "$HERE")"

mkdir -p "$WORK"
cd "$WORK" || exit 1

stage() {
  if [ -d "probityai.github.io-$SITE_COMMIT/atlas/examples/request-effect-join" ]; then
    echo "stage: already present"
    return 0
  fi
  echo "stage: fetching $SITE_COMMIT from the forge"
  $PYTHON - "$SITE_COMMIT" <<'PY' || return 1
import sys, urllib.request, tarfile, io
commit = sys.argv[1]
url = f"https://codeload.github.com/probityai/probityai.github.io/tar.gz/{commit}"
data = urllib.request.urlopen(url, timeout=180).read()
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    if hasattr(tarfile, "data_filter"):
        tar.extractall(".", filter="data")
    else:
        tar.extractall(".")
print(f"staged {len(data)} bytes")
PY
  return 0
}

stage || { echo "FAIL: could not stage the contract"; exit 1; }

CONTRACT_DIR="$WORK/probityai.github.io-$SITE_COMMIT/atlas/examples/request-effect-join"
if [ ! -d "$CONTRACT_DIR" ]; then
  echo "FAIL: contract directory missing at $CONTRACT_DIR"
  exit 1
fi
echo "contract: $CONTRACT_DIR"

# The manifest pins both files. Check before any reader sees them.
$PYTHON - "$CONTRACT_DIR" <<'PY' || { echo "FAIL: manifest digests do not match"; exit 1; }
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
man = json.loads((root / "fixtures" / "MANIFEST.json").read_text(encoding="utf-8"))
declared = man.get("sha256") or {}
# A manifest that pins nothing would verify nothing and report ok. The two files
# this stage exists to authenticate must both be declared.
missing = {"cases.json", "expected.json"} - set(declared)
if missing:
    print("manifest pins no digest for: " + ", ".join(sorted(missing)))
    raise SystemExit(1)
bad = 0
for name, want in declared.items():
    path = root / "fixtures" / name if name == "cases.json" else root / name
    got = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    status = "ok" if got == want else "MISMATCH"
    if got != want:
        bad += 1
    print(f"manifest {name}: {status}")
raise SystemExit(1 if bad else 0)
PY

READER="$HERE_ARG/algovoi_request_effect.py"
CASES="$CONTRACT_DIR/fixtures/cases.json"
EXPECTED="$CONTRACT_DIR/expected.json"

echo
echo "=== reader against expected.json"
$PYTHON "$READER" "$CASES" --expected "$EXPECTED" --json > "$WORK/reader.json"
RC=$?
$PYTHON - "$WORK/reader.json" <<'PY' || { echo "FAIL: the reader's output is not readable"; exit 1; }
import json, sys
from collections import Counter
rows = json.load(open(sys.argv[1], encoding="utf-8"))
c = Counter(r["classification"] for r in rows)
print("classifications:", dict(sorted(c.items())))
for r in rows:
    print(f"  {r['case_id']:6} {r['classification']:16} {' '.join(r['findings']) or '-'}")
PY
echo "reader exit: $RC"

echo
echo "=== every check must bite"
$PYTHON "$HERE_ARG/check_requests_bite.py" "$CASES" "$EXPECTED" \
  || { echo "FAIL: a check is not load-bearing"; exit 1; }

echo
echo "=== the shapes the corpus never builds, each with a control"
$PYTHON "$HERE_ARG/check_named_probes.py" "$CASES" \
  || { echo "FAIL: a named shape is handled wrongly"; exit 1; }

echo
echo "=== canonicaliser against an independent RFC 8785 implementation"
if $PYTHON -c "import rfc8785" 2>/dev/null; then
  ( cd "$HERE" && $PYTHON "$HERE_ARG/differential_jcs.py" "$CONTRACT_DIR/fixtures/cases.json" ) \
    || { echo "FAIL: canonicaliser diverges"; exit 1; }
else
  echo "SKIPPED: rfc8785 not installed, so the canonicaliser was compared to nothing"
  exit 1
fi

echo
if [ "$RC" -eq 0 ]; then
  echo "PASS: reader matched the contract on every case, every check bit, canonicaliser verified"
  exit 0
fi
echo "FAIL: reader diverged from expected.json"
exit 1

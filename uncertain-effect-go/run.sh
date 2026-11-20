#!/usr/bin/env bash
# Build the Go reader and run it through the corpus's external-verifier contract.
#
# Stages the corpus at a pinned commit from the forge, installs the harness from
# PyPI, builds the binary from source with the toolchain it pins, runs the
# corpus's own reference rail first, then this reader behind the same contract.
# Exits non-zero if any stage fails, including a stage that could not run.
set -euo pipefail

COMMIT="${COMMIT:-6ba08cbe9d7f2819e3c47c84adee2fa65390e123}"
HARNESS="${HARNESS:-agent-evidence-vectors==0.37.1}"
GO_VERSION="${GO_VERSION:-1.22.2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"

mkdir -p "$WORK"
cd "$WORK" || exit 1

# Go is a build input rather than a package dependency. Fetch the pinned
# toolchain when the machine has none, so a clean box needs no manual step.
if ! command -v go >/dev/null 2>&1; then
  if [ ! -x /usr/local/go/bin/go ]; then
    echo "go: installing $GO_VERSION"
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
printf 'go: %s\n' "$(go version)"

echo
echo "=== build ==="
cd "$HERE" || exit 1
UNFORMATTED="$(gofmt -l . || true)"
if [ -n "$UNFORMATTED" ]; then
  echo "FAIL: gofmt reports unformatted files: $UNFORMATTED"
  exit 1
fi
echo "gofmt: clean"
go vet ./...
echo "go vet: clean"
go test ./... 2>&1 | tail -3
CGO_ENABLED=0 go build -trimpath -o "$WORK/ue-go" .
printf 'binary: %s (%s bytes, static)\n' "$WORK/ue-go" "$(wc -c < "$WORK/ue-go")"

echo
echo "=== corpus ==="
cd "$WORK" || exit 1
if [ ! -d "corpus-$COMMIT" ]; then
  URL="https://codeload.github.com/probityai/agent-evidence-vectors/tar.gz/$COMMIT"
  if command -v curl >/dev/null 2>&1; then
    curl -sSfL "$URL" -o aev.tgz
  else
    python3 -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1],sys.argv[2])" "$URL" aev.tgz
  fi
  tar xzf aev.tgz
  mv "agent-evidence-vectors-$COMMIT" "corpus-$COMMIT"
  rm aev.tgz
fi
if [ -d "$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect" ]; then
  CORPUS="$WORK/corpus-$COMMIT/corpora/vectors-uncertain-effect"
else
  CORPUS="$WORK/corpus-$COMMIT/vectors-uncertain-effect"
fi

BIN="$WORK/venv/bin"
[ -x "$BIN/python" ] || BIN="$WORK/venv/Scripts"
if [ ! -x "$BIN/python" ] && [ ! -x "$BIN/python.exe" ]; then
  python3 -m venv "$WORK/venv"
  BIN="$WORK/venv/bin"
  [ -x "$BIN/python" ] || BIN="$WORK/venv/Scripts"
  "$BIN/pip" install -q --upgrade pip
  "$BIN/pip" install -q "$HARNESS"
fi
RAIL="$BIN/agent-evidence-vectors"
[ -x "$RAIL" ] || RAIL="$BIN/agent-evidence-vectors.exe"
printf 'harness: %s\n' "$("$BIN/pip" show agent-evidence-vectors | sed -n 's/^Version: //p' | tr -d '\r')"
printf 'corpus commit: %s\n' "$COMMIT"

echo
echo "=== rails ==="
"$RAIL" --vectors "$CORPUS" > reference.log 2>&1 || { tail -3 reference.log; echo "FAIL: the reference rail disagreed"; exit 1; }
printf 'reference rail: %s\n' "$(tail -1 reference.log)"

"$RAIL" --vectors "$CORPUS" --verifier "$WORK/ue-go" > external.log 2>&1 || { tail -5 external.log; echo "FAIL: the Go reader disagreed"; exit 1; }
printf 'go reader:      %s\n' "$(tail -1 external.log)"

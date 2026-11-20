#!/usr/bin/env bash
# Reproduce the AlgoVoi reader's run over the uncertain-effect/v1 corpus.
#
# Runs twice: once through the corpus's reference rail, to show the corpus and
# the harness agree before our reader is involved, and once through the
# external-verifier contract with this reader. Writes both logs and a
# machine-readable summary, and exits non-zero if either rail disagrees.
#
# Needs: python3 with venv, network once (pip and the corpus tarball), bash.
set -euo pipefail

COMMIT="${COMMIT:-6ba08cbe9d7f2819e3c47c84adee2fa65390e123}"
HARNESS="${HARNESS:-agent-evidence-vectors==0.37.1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HERE/.run}"

mkdir -p "$WORK"
cd "$WORK"

if [ ! -d "corpus-$COMMIT" ]; then
  URL="https://codeload.github.com/probityai/agent-evidence-vectors/tar.gz/$COMMIT"
  if command -v curl >/dev/null 2>&1; then
    curl -sSfL "$URL" -o aev.tgz
  else
    # python:*-slim images carry no curl; fetch with the interpreter under test.
    python3 -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$URL" aev.tgz
  fi
  tar xzf aev.tgz
  mv "agent-evidence-vectors-$COMMIT" "corpus-$COMMIT"
  rm aev.tgz
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
  python3 -m venv "$WORK/venv"
  BIN="$(venv_bin)"
  "$BIN/pip" install -q --upgrade pip
  "$BIN/pip" install -q "$HARNESS"
fi
BIN="$(venv_bin)"
RAIL="$BIN/agent-evidence-vectors"
[ -x "$RAIL" ] || RAIL="$BIN/agent-evidence-vectors.exe"
printf 'harness: %s\n' "$("$BIN/pip" show agent-evidence-vectors | sed -n 's/^Version: //p')"
printf 'corpus commit: %s\n' "$COMMIT"
printf 'reader sha256: %s\n' "$(sha256sum "$HERE/algovoi_uncertain_effect.py" | cut -d' ' -f1)"

rc=0
"$RAIL" --vectors "$CORPUS" > reference.log 2>&1 || rc=1
printf 'reference rail: %s\n' "$(tail -1 reference.log)"

# A verifier command is a string the harness hands to a child process. On MSYS the
# /c/... form bash prints is not translated for a native interpreter, and a child
# that cannot open the reader reports every case as a disagreement, which reads as
# a failing reader rather than a broken harness call. Use the Windows form of both
# pieces when the host is Windows.
VERIFIER="python3 $HERE/algovoi_uncertain_effect.py"
WINDOWS_PATHS=0
case "$(uname -s)" in
  MSYS*|MINGW*|CYGWIN*)
    if command -v cygpath >/dev/null 2>&1; then
      WINDOWS_PATHS=1
      PY_FOR_CHILD="$BIN/python"
      [ -x "$PY_FOR_CHILD" ] || PY_FOR_CHILD="$BIN/python.exe"
      VERIFIER="$(cygpath -m "$PY_FOR_CHILD") $(cygpath -m "$HERE/algovoi_uncertain_effect.py")"
    fi
    ;;
esac

our_rc=0
"$RAIL" --vectors "$CORPUS" --verifier "$VERIFIER" \
  --report "$WORK/external-report.json" > external.log 2>&1 || our_rc=1
printf 'algo reader:    %s\n' "$(tail -1 external.log)"

case "$rc$our_rc" in
  00) ;;
  *)  echo "a rail disagreed; see $WORK/reference.log and $WORK/external.log"; exit 1 ;;
esac

# The reader's own derivations, checked against the corpus declarations.
PY="$BIN/python"
[ -x "$PY" ] || PY="$BIN/python.exe"
# The interpreter is native on Windows and does not translate an MSYS path, so
# every path handed to it is converted to the mixed form first.
SCRIPTS="$HERE"
READER_ARG="$HERE/algovoi_uncertain_effect.py"
CORPUS_ARG="$CORPUS"
if [ "$WINDOWS_PATHS" -eq 1 ]; then
  SCRIPTS="$(cygpath -m "$HERE")"
  READER_ARG="$(cygpath -m "$HERE/algovoi_uncertain_effect.py")"
  CORPUS_ARG="$(cygpath -m "$CORPUS")"
fi
"$PY" "$SCRIPTS/report_axes.py" "$CORPUS_ARG" --reader "$READER_ARG" \
  | tee "$WORK/axes.txt"
rc_axes=${PIPESTATUS[0]}
[ "$rc_axes" -eq 0 ] || exit 1

# The strictnesses the corpus cannot see, each with a control that must pass.
"$PY" "$SCRIPTS/check_audit_probes.py" --reader "$READER_ARG" \
  | tee "$WORK/probes.txt"
rc_probes=${PIPESTATUS[0]}
[ "$rc_probes" -eq 0 ] || exit 1
exit 0

"""Differential test: this reader's canonicaliser against an independent RFC 8785.

The reader under `readers/request-effect` implements JCS by hand. Its own test
vectors were written by the same hand, so agreement there proves the two agree
with each other and nothing more. This script compares the canonical bytes
against `rfc8785` (a PyPI package the reader's author did not write) over the
whole fixture corpus plus generated records, so a wrong digest has somewhere to
show up.

Run in a container that has `rfc8785` installed:
    python3 differential_jcs.py
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import rfc8785

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "algovoi_request_effect", HERE / "algovoi_request_effect.py")
if spec is None or spec.loader is None:
    raise SystemExit(f"could not load {HERE / 'algovoi_request_effect.py'}")
R = importlib.util.module_from_spec(spec)
sys.modules["algovoi_request_effect"] = R
spec.loader.exec_module(R)

# The case file is staged from the contract commit, so take it from argv when the
# clean-room run supplies one; fall back to the copy beside this script.
cases_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "cases.json"
cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]


def compare(value, label: str, diffs: list[str]) -> None:
    try:
        mine = R._canonical(value).encode("utf-8")
    except Exception as exc:  # noqa: BLE001
        mine = f"<raised {type(exc).__name__}: {exc}>".encode()
    try:
        theirs = rfc8785.dumps(value)
    except Exception as exc:  # noqa: BLE001
        theirs = f"<raised {type(exc).__name__}: {exc}>".encode()
    if mine != theirs:
        diffs.append(f"{label}\n    mine   = {mine!r}\n    theirs = {theirs!r}")


diffs: list[str] = []

# 1. Every preimage in the corpus.
preimages = 0
for case in cases:
    for field in ("approved", "effective", "decision", "covering_decision",
                  "attempt", "result"):
        obj = case.get(field)
        if obj:
            preimages += 1
            compare(obj["preimage"], f"{case['case_id']}.{field}.preimage", diffs)

# 2. Every ref in the corpus must be the digest rfc8785 derives. S-07 is the
#    corpus's own digest-mismatch case: its stub ref is meant to disagree with its
#    preimage, so a divergence there is the corpus being right, not us being wrong.
refs = 0
for case in cases:
    for field in ("approved", "effective", "decision", "covering_decision",
                  "attempt", "result"):
        obj = case.get(field)
        if obj:
            refs += 1
            import hashlib
            want = "sha256:" + hashlib.sha256(
                rfc8785.dumps(obj["preimage"])).hexdigest()
            if obj["ref"] != want:
                if case["case_id"] == "S-07":
                    print(f"note: S-07.{field} ref disagrees with its preimage, "
                          "as the corpus intends")
                    continue
                diffs.append(f"{case['case_id']}.{field}: ref {obj['ref']} != "
                             f"rfc8785-derived {want}")

# 3. Generated values in the shapes that split the two implementations.
rng = random.Random(20261101)  # nosec B311 - seeded test data, no security use
KEYS = ["", "a", "A", "z", "0", "1", "\n", " ", "\u007f", "\u0080", "\u20ac",
        "\ufb33", "\U0001f600", "\ud800", "key with space", "Ω", "e"]
STRINGS = ["", "a", "\x00", "\x1f", "quote\"", "back\\slash", "\u007f", "\u0080",
           "\u2028", "\u2029", "\U0001f600", "Ω", "tab\t", newline_ok := "line\n"]
NUMBERS = [0, 1, -1, 0.0, -0.0, 1e21, 1e-7, 1.5, -1.5, 333333333.33333329,
           2**53 - 1, -(2**53 - 1), 1e-300, 123.456, 1e6, 1e-6, 0.1, 3.0]

# The I-JSON safe-integer boundary. JCS refuses an integer that no double holds,
# and this reader must refuse it too or its digest disagrees with every other JCS
# implementation on the same record. These are compared separately because the two
# implementations satisfy the requirement in different ways: rfc8785 raises, this
# reader raises InvalidInput, and the agreement being tested is "both refuse".
BOUNDARY = [2**53 - 2, 2**53 - 1, 2**53, 2**53 + 1, 10**15, 10**18, 10**21,
            -(2**53), -(2**53 + 1)]

for i in range(400):
    kind = i % 4
    if kind == 0:
        value = {rng.choice(KEYS): rng.choice(NUMBERS) for _ in range(rng.randint(0, 5))}
    elif kind == 1:
        value = [rng.choice(STRINGS + NUMBERS) for _ in range(rng.randint(0, 4))]
    elif kind == 2:
        value = {rng.choice(KEYS): {rng.choice(KEYS): rng.choice(STRINGS)}
                 for _ in range(rng.randint(0, 3))}
    else:
        value = rng.choice(STRINGS + NUMBERS + [True, False, None])
    compare(value, f"generated[{i}] {json.dumps(value, default=str)[:80]}", diffs)

print(f"preimages compared: {preimages}")
print(f"refs re-derived independently: {refs}")
print(f"generated values compared: 400")

# 4. The safe-integer boundary: both implementations must refuse the same values.
boundary_checked = 0
for value in BOUNDARY:
    try:
        mine = R._canonical(value).encode("utf-8")
    except Exception as exc:  # noqa: BLE001
        mine = f"<refused {type(exc).__name__}>".encode()
    try:
        theirs = rfc8785.dumps(value)
    except Exception as exc:  # noqa: BLE001
        theirs = f"<refused {type(exc).__name__}>".encode()
    mine_refused = mine.startswith(b"<refused")
    theirs_refused = theirs.startswith(b"<refused")
    boundary_checked += 1
    if mine_refused != theirs_refused or (not mine_refused and mine != theirs):
        diffs.append(f"boundary {value}: mine={mine!r} theirs={theirs!r} "
                     f"(refused: mine={mine_refused} theirs={theirs_refused})")
print(f"safe-integer boundary values compared: {boundary_checked}")

if diffs:
    print(f"\n{len(diffs)} divergence(s):")
    for d in diffs[:20]:
        print("  " + d)
    raise SystemExit(1)
print("\n0 divergences: the reader's canonical bytes and digests match rfc8785 "
      f"{getattr(rfc8785, '__version__', '')} on every case and generated value")

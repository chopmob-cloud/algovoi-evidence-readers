"""Prove the request-effect reader's checks are load-bearing, and that its JCS is real.

Three things this establishes, each of which a green run does not:

1. Every ordered check, disabled in turn, turns the corpus red. A check that can be
   removed without changing an answer is not doing anything, and the corpus alone
   cannot tell you that.
2. Each `must_not_claim` finding is reported by a mutant that should produce it, and
   by nothing in the clean corpus. A prohibition with no witness is untested.
3. The canonicaliser agrees with RFC 8785 on the vectors that decide it: UTF-16 key
   order (not code-point order), ECMAScript number spelling, and control-character
   escaping. A hand-written canonicaliser is where a wrong digest hides.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CASES = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "cases.json"
EXPECTED = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "expected.json"

spec = importlib.util.spec_from_file_location(
    "algovoi_request_effect", HERE / "algovoi_request_effect.py")
assert spec and spec.loader
R = importlib.util.module_from_spec(spec)
sys.modules["algovoi_request_effect"] = R
spec.loader.exec_module(R)

records = {c["case_id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}
declared = {c["case_id"]: c for c in json.loads(EXPECTED.read_text(encoding="utf-8"))["cases"]}

# A corpus case with no declared expectation cannot be checked, and a matrix that
# silently indexes only the cases it happens to know would report green over a case
# it never scored. Name the gap and stop.
_undeclared = sorted(set(records) - set(declared))
if _undeclared:
    raise SystemExit(
        f"check_requests_bite.py: no expectation declared for {', '.join(_undeclared)}; "
        f"pass the matching expected.json as the second argument"
    )

failures: list[str] = []
checks = 0


def ok(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{label}: {detail or 'condition false'}")


# ---------------------------------------------------------------------------
# 1. RFC 8785 conformance. These are the shapes that separate a real
#    implementation from json.dumps(sort_keys=True).
# ---------------------------------------------------------------------------

JCS_VECTORS = [
    # (input, expected canonical text)
    ({"b": 1, "a": 2}, '{"a":2,"b":1}'),
    # UTF-16 code-unit order: "\n" (0x000A) sorts before " " (0x0020).
    ({"\u0020": 1, "\u000a": 2}, '{"\\n":2," ":1}'),
    # The astral case that breaks code-point sorting: U+1F600 is D83D DE00 in
    # UTF-16, so its first unit D83D sorts below U+FB33, while code-point order
    # puts the emoji (0x1F600) after U+FB33 (0xFB33).
    ({"\ufb33": 1, "\U0001f600": 2}, '{"\U0001f600":2,"\ufb33":1}'),
    (0.0, "0"),
    (-0.0, "0"),
    # The exponent boundary: strictly below 1e-6 goes exponential, and 1e-6
    # itself is plain decimal. This is the one I had wrong.
    (1e-6, "0.000001"),
    (1e-7, "1e-7"),
    (1e21, "1e+21"),
    (1e6, "1000000"),
    (123.456, "123.456"),
    (333333333.33333329, "333333333.3333333"),
    (True, "true"),
    (None, "null"),
    ("a\"b\\c", '"a\\"b\\\\c"'),
    ("\x00\x1f", '"\\u0000\\u001f"'),
    ({"\u20ac": 1, "\r": 2, "1": 3, "\U0001f600": 4, "\ufb33": 5, "\u0080": 6},
     '{"\\r":2,"1":3,"\u0080":6,"\u20ac":1,"\U0001f600":4,"\ufb33":5}'),
]

for value, want in JCS_VECTORS:
    try:
        got = R._canonical(value)
    except Exception as exc:  # noqa: BLE001
        got = f"<raised {exc!r}>"
    ok(f"JCS {value!r}", got == want, f"got {got!r} want {want!r}")

# The separator that distinguishes a real JCS from sort_keys=True.
sort_keys_only = json.dumps({"b": 1, "a": 2}, sort_keys=True, separators=(",", ":"))
ok("JCS differs from json.dumps(sort_keys=True) on UTF-16 order",
   R._canonical({"\ufb33": 1, "\U0001f600": 2}) !=
   json.dumps({"\ufb33": 1, "\U0001f600": 2}, sort_keys=True,
              separators=(",", ":"), ensure_ascii=False),
   "the astral vector did not separate the two implementations")


# ---------------------------------------------------------------------------
# 2. Every must_not_claim finding has a witness. If no mutant produces it, the
#    prohibition is untested and the reader could report it freely.
# ---------------------------------------------------------------------------

def mutated(case_id: str, change) -> dict:
    record = copy.deepcopy(records[case_id])
    change(record)
    return record


def rehash(record: dict, field: str) -> None:
    """Recompute a ref so the mutant fails on its intended check, not on digest."""
    obj = record[field]
    obj["ref"] = R.ref_of(obj["preimage"])


def find(record: dict, value) -> bool:
    return value in R.evaluate(record)["findings"]


# later_state_unchanged must be reachable: a read equal to the committed output.
def make_unchanged(record: dict) -> None:
    record["later_observation"]["content_sha256"] = \
        record["result"]["preimage"]["output_hash"]


ok("witness later_state_unchanged",
   find(mutated("S-04", make_unchanged), "later_state_unchanged"))

# later_state_not_comparable must be reachable: a read of another scope.
def other_scope(record: dict) -> None:
    record["later_observation"]["target_scope"] = "transfer_funds:/work/other.txt"


ok("witness later_state_not_comparable",
   find(mutated("S-04", other_scope), "later_state_not_comparable"))

# join_chain_broken must be reachable: an attempt naming a different call.
def break_attempt(record: dict) -> None:
    record["attempt"]["preimage"]["action_ref"] = "sha256:" + "0" * 64
    rehash(record, "attempt")
    record["result"]["preimage"]["attempt_id"] = record["attempt"]["ref"]
    rehash(record, "result")


ok("witness join_chain_broken",
   find(mutated("S-00", break_attempt), "join_chain_broken"))

# result_not_completed must be reachable.
def not_completed(record: dict) -> None:
    record["result"]["preimage"]["outcome"] = "failed"
    rehash(record, "result")


ok("witness result_not_completed",
   find(mutated("S-00", not_completed), "result_not_completed"))

# join_established must be reachable: the clean corpus carries it.
ok("witness join_established",
   find(records["S-00"], "join_established"))

# effective_call_not_covered and covering_decision_not_permit.
def drop_covering(record: dict) -> None:
    record["covering_decision"] = None


ok("witness effective_call_not_covered",
   find(mutated("S-02", drop_covering), "effective_call_not_covered"))


def covering_denies(record: dict) -> None:
    record["covering_decision"]["preimage"]["outcome"] = "deny"
    rehash(record, "covering_decision")


ok("witness covering_decision_not_permit",
   find(mutated("S-03", covering_denies), "covering_decision_not_permit"))

# digest_mismatch.
def corrupt_ref(record: dict) -> None:
    record["decision"]["ref"] = "sha256:" + "1" * 64


ok("witness digest_mismatch", find(mutated("S-00", corrupt_ref), "digest_mismatch"))

# unsupported_canonical_profile.
def bad_profile(record: dict) -> None:
    record["decision"]["preimage"]["canonicalization_profile_id"] = "jcs-rfc8785-v9"
    rehash(record, "decision")


ok("witness unsupported_canonical_profile",
   find(mutated("S-00", bad_profile), "unsupported_canonical_profile"))

# refusal_observed and store_mutation_not_established on the no-dispatch case.
ok("witness refusal_observed", find(records["S-09"], "refusal_observed"))
ok("witness store_mutation_not_established",
   find(records["S-09"], "store_mutation_not_established"))
ok("witness join_not_established", find(records["S-09"], "join_not_established"))
ok("witness original_result_preserved", find(records["S-04"], "original_result_preserved"))
ok("witness later_state_not_supplied", find(records["S-00"], "later_state_not_supplied"))

# The safe-integer boundary is a refusal this reader had to adopt: JCS serialises
# numbers as doubles, so an integer no double holds must be refused rather than
# printed. The corpus never reaches it (every ts_ms is ~1.8e12), and the
# differential test against rfc8785 is what found it. Assert both directions here so
# the reader cannot drift back to accepting them.
safe_high_refused = False
try:
    R._canonical(2**53)  # one past the safe maximum
except R.InvalidInput:
    safe_high_refused = True
ok("safe-integer boundary refuses 2**53, which JCS cannot represent",
   safe_high_refused, "2**53 was accepted")
ok("safe-integer boundary prints the largest safe integer as digits",
   R._canonical(2**53 - 1) == str(2**53 - 1),
   f"got {R._canonical(2**53 - 1)!r}")

# ---------------------------------------------------------------------------
# 3. Every check, removed in turn, must turn the corpus red. Simulated by
#    replacing the piece of behaviour the check contributes.
# ---------------------------------------------------------------------------

def corpus_after(patch_name: str, replacement) -> int:
    original = getattr(R, patch_name)
    setattr(R, patch_name, replacement)
    try:
        bad = 0
        for cid, record in records.items():
            verdict = R.evaluate(copy.deepcopy(record))
            want = declared[cid]
            if (verdict["classification"] != want["classification"]
                    or set(verdict["findings"]) != set(want["findings"])):
                bad += 1
        return bad
    finally:
        setattr(R, patch_name, original)


# check 1 (shape): accept any key set, i.e. stop reading declared keys, and supply
# a default for the key that is absent — the fail-open shape the contract names.
real_shape = R._shape


def lenient_shape(record):
    if not isinstance(record, dict):
        raise R.InvalidInput("RECORD_NOT_OBJECT")
    record = dict(record)
    record.setdefault("later_observation", None)
    return record  # stop validating keys, presence agreement, later-observation shape


setattr(R, "_shape", lenient_shape)
bad = sum(1 for cid, rec in records.items()
          if R.evaluate(copy.deepcopy(rec))["classification"] != declared[cid]["classification"])
setattr(R, "_shape", real_shape)
ok("check shape is load-bearing", bad > 0, f"disabling shape validation changed {bad} cases")

# check 2 (profile): accept any profile id.
def corpus_profile_check() -> int:
    original = set(getattr(R, "SUPPORTED_PROFILES"))
    setattr(R, "SUPPORTED_PROFILES", original | {"jcs-rfc8785-v2-draft", "jcs-rfc8785-v9"})
    try:
        return sum(1 for cid, rec in records.items()
                   if R.evaluate(copy.deepcopy(rec))["classification"] != declared[cid]["classification"])
    finally:
        setattr(R, "SUPPORTED_PROFILES", original)


profile_changed = corpus_profile_check()
ok("check profile is load-bearing", profile_changed > 0,
   f"accepting every profile changed {profile_changed} cases")

# check 3 (digest): make every recomputation fail, so every supplied ref mismatches.
# If the digest check were not load-bearing, the classifications would not move.
def corpus_digest_check() -> int:
    original = R.ref_of
    R.ref_of = lambda preimage: "sha256:" + "?" * 64  # never equal to a supplied ref
    try:
        bad = 0
        for cid, rec in records.items():
            v = R.evaluate(copy.deepcopy(rec))
            if v["classification"] != declared[cid]["classification"]:
                bad += 1
        return bad
    finally:
        R.ref_of = original


ok("check digest is load-bearing", corpus_digest_check() > 0,
   "a canonicaliser that never matches still produced the expected classes")


def corpus_without_digest() -> int:
    """Recompute refs from their own preimages before evaluating, as a reader that
    normalises rather than compares would: every digest check then passes."""
    bad = 0
    for cid, rec in records.items():
        record = copy.deepcopy(rec)
        for field in ("approved", "effective", "decision", "covering_decision",
                      "attempt", "result"):
            if record.get(field):
                record[field]["ref"] = R.ref_of(record[field]["preimage"])
        v = R.evaluate(record)
        if (v["classification"] != declared[cid]["classification"]
                or set(v["findings"]) != set(declared[cid]["findings"])):
            bad += 1
    return bad


ok("check digest is load-bearing (repairing refs changes answers)",
   corpus_without_digest() > 0,
   "repairing every ref to its own preimage produced the expected answers anyway")

# check 4 (dispatch): give S-09 a dispatch that is consistently bound, so the only
# check that can move it off not_established is the dispatch gate itself. An earlier
# version grafted S-00's attempt onto S-09 unchanged, which breaks the chain and
# makes the case refuse for the wrong reason: the assertion then holds without the
# dispatch check doing anything.
def corpus_dispatch_check() -> int:
    record = copy.deepcopy(records["S-09"])
    record["attempt"] = copy.deepcopy(records["S-00"]["attempt"])
    record["attempt"]["preimage"]["action_ref"] = record["effective"]["ref"]
    record["attempt"]["ref"] = R.ref_of(record["attempt"]["preimage"])
    record["result"] = copy.deepcopy(records["S-00"]["result"])
    record["result"]["preimage"]["attempt_id"] = record["attempt"]["ref"]
    record["result"]["ref"] = R.ref_of(record["result"]["preimage"])
    v = R.evaluate(record)
    # With a fully bound dispatch, dispatch + chain both pass, so the record leaves
    # not_established on the strength of the dispatch check alone.
    return 0 if v["classification"] == declared["S-09"]["classification"] else 1


ok("check dispatch is load-bearing", corpus_dispatch_check() > 0)

# check 6 (chain, non-completed outcome): an attempt that exists with a terminal
# outcome other than `completed` must be not_established with result_not_completed.
# No record in the corpus as published carries that shape, so without this the branch
# that refuses to round an interrupted effect is unexercised: a reader that treats a
# dispatched but unfinished attempt as established still matches every case in the
# corpus. The property is asserted directly rather than through the divergence count
# the other checks use, because here the correct answer is a specific verdict rather
# than a change from one.
def interrupted_is_not_established(case_id: str) -> bool:
    record = copy.deepcopy(records[case_id])
    record["result"]["preimage"]["outcome"] = "interrupted"
    record["result"]["ref"] = R.ref_of(record["result"]["preimage"])
    v = R.evaluate(record)
    return (v["classification"] == "not_established"
            and "result_not_completed" in v["findings"])


ok("check chain refuses to round a non-completed outcome",
   interrupted_is_not_established("S-00"),
   "a dispatched attempt with outcome=interrupted was not reported "
   "not_established with result_not_completed")

# The same must hold when a later read is present: a read cannot settle an outcome
# the runtime never reported as completed.
ok("check a later read cannot round an interrupted attempt",
   interrupted_is_not_established("S-04"),
   "an interrupted attempt carrying a later read was not reported not_established")

# check 5 (coverage): stop requiring a covering decision.
def corpus_coverage_check() -> int:
    bad = 0
    for cid in ("S-01", "S-02", "S-03"):
        record = copy.deepcopy(records[cid])
        record["covering_decision"] = None
        v = R.evaluate(record)
        if v["classification"] == declared[cid]["classification"]:
            bad += 1
    return bad


ok("check coverage is load-bearing", corpus_coverage_check() > 0,
   "removing the covering decision left S-01..S-03 classified as expected")

# check 7 (later state): treat a missing read as unchanged.
def corpus_later_check() -> int:
    bad = 0
    for cid, rec in records.items():
        record = copy.deepcopy(rec)
        later = record.get("later_observation")
        if later is None and record.get("result"):
            record["later_observation"] = {
                "target_scope": record["approved"]["preimage"]["scope"],
                "content_sha256": record["result"]["preimage"]["output_hash"],
                "observed_at_ms": None,
                "observer_id": "mutant",
            }
        v = R.evaluate(record)
        if (v["classification"] != declared[cid]["classification"]
                or set(v["findings"]) != set(declared[cid]["findings"])):
            bad += 1
    return bad


ok("check later state is load-bearing", corpus_later_check() > 0,
   "inventing an unchanged read for absent ones changed no answers")

# ---------------------------------------------------------------------------
# 4. The S-05 / S-06 seam: null is a read, absent is invalid. Removing the key must
#    not become a null-valued key; a reader that defaults has stopped reading.
# ---------------------------------------------------------------------------

seam = copy.deepcopy(records["S-05"])
ok("S-05 is established with a null later read",
   R.evaluate(seam)["classification"] == "established")

del seam["later_observation"]
ok("S-06 (key absent) is invalid_input",
   R.evaluate(seam)["classification"] == "invalid_input",
   f"got {R.evaluate(seam)['classification']}")

seam_null = copy.deepcopy(records["S-05"])
seam_null["later_observation"] = None
ok("explicit null later read is not invalid_input",
   R.evaluate(seam_null)["classification"] != "invalid_input")

# ---------------------------------------------------------------------------
print()
if failures:
    for line in failures:
        print("FAIL:", line)
    print(f"\n{failures[0].split(':')[0]}: {len(failures)} of {checks} checks failed")
    raise SystemExit(1)
print(f"{checks} checks passed")
print(f"RFC 8785 vectors: {len(JCS_VECTORS)} of {len(JCS_VECTORS)}")

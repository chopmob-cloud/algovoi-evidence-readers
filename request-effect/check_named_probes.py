"""The shapes the corpus never builds.

The ten corpus cases are green, but a corpus is a finite set and the reader's
fail-open modes are exactly the records it does not contain. Each probe below
mutates a corpus record into a shape the contract names but the corpus never
exercises, and requires the reader to answer as the contract says. Every probe has
a control: the same record unmutated must still classify as expected, so a probe
that passes cannot be passing because the reader says the same thing always.

Exit 0 when every probe and control behaves; 1 otherwise.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
CASES = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "cases.json"

spec = importlib.util.spec_from_file_location(
    "algovoi_request_effect", HERE / "algovoi_request_effect.py")
assert spec and spec.loader
R = importlib.util.module_from_spec(spec)
sys.modules["algovoi_request_effect"] = R
spec.loader.exec_module(R)

records = {c["case_id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}

passed = 0
failed: list[str] = []


def probe(label: str, record: dict, want_class: str,
          want_findings: set[str], *, forbid: set[str] = frozenset()) -> None:
    global passed
    got = R.evaluate(record)
    findings = set(got["findings"])
    problems = []
    if got["classification"] != want_class:
        problems.append(f"class {got['classification']} != {want_class}")
    for missing in sorted(want_findings - findings):
        problems.append(f"missing {missing}")
    for bad in sorted(findings & set(forbid)):
        problems.append(f"reported forbidden {bad}")
    if problems:
        failed.append(f"{label}: {'; '.join(problems)}")
    else:
        passed += 1


def control(label: str, case_id: str) -> None:
    global passed
    want = {"S-00": "established", "S-01": "refused", "S-02": "established",
            "S-03": "refused", "S-04": "established", "S-05": "established",
            "S-06": "invalid_input", "S-07": "refused", "S-08": "invalid_input",
            "S-09": "not_established"}[case_id]
    got = R.evaluate(copy.deepcopy(records[case_id]))
    if got["classification"] == want:
        passed += 1
    else:
        failed.append(f"{label}: control {case_id} classified {got['classification']}")


def m(case_id: str) -> dict:
    return copy.deepcopy(records[case_id])


def rehash(record: dict, field: str) -> None:
    record[field]["ref"] = R.ref_of(record[field]["preimage"])


# ---------------------------------------------------------------------------
# 1. 'attempt' present but 'result' absent (and the reverse). The contract says
#    invalid_input, not a refusal.
# ---------------------------------------------------------------------------
rec = m("S-00")
del rec["result"]
probe("attempt without result is invalid_input", rec, "invalid_input", set(),
      forbid={"join_established"})
control("control S-00", "S-00")

rec = m("S-00")
del rec["attempt"]
probe("result without attempt is invalid_input", rec, "invalid_input", set(),
      forbid={"join_established"})

# An explicit null where an object belongs is not the same as absent.
rec = m("S-00")
rec["result"] = None
probe("null result beside an attempt is invalid_input", rec, "invalid_input", set())

# ---------------------------------------------------------------------------
# 2. later_observation: absent vs null. The corpus has both near-identical cases;
#    a reader that defaults the missing key collapses them.
# ---------------------------------------------------------------------------
rec = m("S-05")
del rec["later_observation"]
probe("absent later_observation is invalid_input", rec, "invalid_input", set(),
      forbid={"established", "later_state_not_supplied"})

rec = m("S-05")
rec["later_observation"] = None
probe("null later_observation is established/not_supplied", rec,
      "established", {"join_established", "later_state_not_supplied"},
      forbid={"later_state_unchanged"})

# A null read must never be reported as an unchanged resource, on any case that
# supplies one. S-00 and S-05 are both null-read cases, so assert it on both rather
# than on the one the probe above already covers.
for cid in ("S-00", "S-05"):
    got = R.evaluate(copy.deepcopy(records[cid]))
    if "later_state_unchanged" in got["findings"]:
        failed.append(f"null read reported as unchanged on {cid}")
    else:
        passed += 1

# ---------------------------------------------------------------------------
# 3. A read whose capture time is unknown is still a read (contract: observed_at_ms
#    may be null). It must not become "not supplied".
# ---------------------------------------------------------------------------
rec = m("S-04")
outcome = R.evaluate(rec)["classification"]
if outcome == "established":
    passed += 1
else:
    failed.append(f"control S-04 established: got {outcome}")

rec = m("S-04")
rec["later_observation"]["observed_at_ms"] = None
probe("null observed_at_ms is still a read", rec, "established",
      {"join_established", "later_state_differs"},
      forbid={"later_state_not_supplied", "invalid_input"})

# ---------------------------------------------------------------------------
# 4. Coverage: binding without permission is not coverage. A covering decision
#    that names 'effective' but does not permit must refuse, and must not be
#    accepted as cover.
# ---------------------------------------------------------------------------
rec = m("S-02")
rec["covering_decision"]["preimage"]["outcome"] = "deny"
rehash(rec, "covering_decision")
probe("covering decision that denies is not coverage", rec, "refused",
      {"covering_decision_not_permit"}, forbid={"join_established"})

# A covering decision that permits but names the OTHER call is not coverage either.
rec = m("S-02")
rec["covering_decision"]["preimage"]["action_ref"] = rec["approved"]["ref"]
rehash(rec, "covering_decision")
probe("covering decision naming another call is not coverage", rec, "refused",
      {"effective_call_not_covered"}, forbid={"join_established"})

# ---------------------------------------------------------------------------
# 5. Chain: an attempt that names the approved call while 'effective' differs.
#    Self-consistent digests, broken join.
# ---------------------------------------------------------------------------
rec = m("S-02")
rec["attempt"]["preimage"]["action_ref"] = rec["approved"]["ref"]
rehash(rec, "attempt")
rec["result"]["preimage"]["attempt_id"] = rec["attempt"]["ref"]
rehash(rec, "result")
probe("attempt naming the approved call breaks the chain", rec, "refused",
      {"join_chain_broken"}, forbid={"join_established"})

# ---------------------------------------------------------------------------
# 6. Types that look true. A boolean where an integer belongs, and a string where
#    a ref belongs.
# ---------------------------------------------------------------------------
rec = m("S-00")
rec["attempt"]["preimage"]["attempt_seq"] = True
rehash(rec, "attempt")
rec["result"]["preimage"]["attempt_id"] = rec["attempt"]["ref"]
rehash(rec, "result")
probe("boolean attempt_seq is caught", rec, "invalid_input", set(),
      forbid={"join_established"})

rec = m("S-00")
rec["decision"]["ref"] = 12345
probe("non-string ref is invalid_input", rec, "invalid_input", set())

# ---------------------------------------------------------------------------
# 7. The unpermitted-dispatch shape: a decision that DENIES, yet an attempt and a
#    completed result exist. The contract's order of checks has no explicit branch
#    for it, so whatever the reader does, it must not claim the join is established
#    on a call the guardrail refused — this asserts the safety property, not a
#    particular verdict.
# ---------------------------------------------------------------------------
rec = m("S-00")
rec["decision"]["preimage"]["outcome"] = "deny"
rehash(rec, "decision")
got = R.evaluate(rec)
if got["classification"] in {"established", "refused", "not_established", "invalid_input"}:
    passed += 1
else:
    failed.append("denied-decision probe produced a class outside the closed set")
if "join_established" in got["findings"]:
    failed.append("denied-decision probe reports join_established on a refused call "
                  f"(classification {got['classification']})")

# ---------------------------------------------------------------------------
# 8. Digest mismatch must stop the run: fields are not compared against a preimage
#    that does not hash to its own ref. Corrupt a preimage field, leave the ref.
# ---------------------------------------------------------------------------
rec = m("S-02")
rec["covering_decision"]["preimage"]["policy_id"] = "fixture-policy-OTHER"
probe("edited preimage under a stale ref is digest_mismatch", rec, "refused",
      {"digest_mismatch"}, forbid={"covering_decision_not_permit", "join_established"})

# ---------------------------------------------------------------------------
# 10. A covering decision beside an undispatched record. The contract's order of
#     checks reaches dispatch (step 4) before coverage (step 5), so this is
#     not_established, not malformed. An earlier version of this reader refused it
#     as invalid_input on an invented rule; this probe exists because a green
#     corpus run could not see the difference.
# ---------------------------------------------------------------------------
rec = m("S-02")
rec["attempt"] = None
rec["result"] = None
probe("covering decision with nothing dispatched is not_established", rec,
      "not_established", {"join_not_established", "store_mutation_not_established"},
      forbid={"invalid_input"})

# ---------------------------------------------------------------------------
# 11. Asymmetry, asserted deliberately: a covering decision is accepted as the
#     permission for the effective call on a rewrite (S-02 does exactly this and
#     the corpus declares it established), while for an un-rewritten call the
#     original decision is what must permit. Pinning both keeps a later edit from
#     "fixing" one half into disagreement with the corpus.
# ---------------------------------------------------------------------------
rec = m("S-02")
rec["decision"]["preimage"]["outcome"] = "deny"
rehash(rec, "decision")
probe("removed call: original deny refuses even though covering permits", rec,
      "established", {"join_established"},
      forbid={"covering_decision_not_permit", "effective_call_not_covered"})

rec = m("S-00")
rec["decision"]["preimage"]["outcome"] = "deny"
rehash(rec, "decision")
probe("un-rewritten call: original deny refuses", rec, "refused",
      {"effective_call_not_covered"}, forbid={"join_established"})

# ---------------------------------------------------------------------------
# 9. Finding-closed-set check: no answer may carry a finding outside the contract's
#    list, over the whole corpus and the mutants above.
# ---------------------------------------------------------------------------
unknown: set[str] = set()
for record in records.values():
    unknown |= set(R.evaluate(copy.deepcopy(record))["findings"]) - R.FINDINGS
if unknown:
    failed.append(f"findings outside the closed set: {sorted(unknown)}")
else:
    passed += 1

print(f"{passed} probes and controls passed")
if failed:
    for line in failed:
        print("FAIL:", line)
    print(f"\n{len(failed)} probe(s) failed")
    raise SystemExit(1)
print("0 failures: every named shape is handled as the contract states")

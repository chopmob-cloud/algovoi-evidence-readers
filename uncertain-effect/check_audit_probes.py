#!/usr/bin/env python3
"""Pin the four strictnesses the corpus cannot see, each with its control.

The corpus's 27 cases never vary a reconciliation key, never omit a report,
never put a non-count in `ownerEffects`, and never pair a lease claim with an
absent `leases` array, so a reader can fail open on all four and still score
27 of 27. An outside audit found four such holes in this reader; this script
holds them shut, and each probe is paired with a control that must still be
allowed, so a reader that simply refuses everything fails here too.

    python3 check_audit_probes.py            # uses ./algovoi_uncertain_effect.py
    python3 check_audit_probes.py --reader <path>
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

CONTRACT = "https://probityai.github.io/agent-evidence-vectors/predicate/v1/uncertain-effect"
DIGEST = "sha256:" + "4" * 64


def load_reader(path: Path):
    spec = importlib.util.spec_from_file_location("algovoi_uncertain_effect", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load the reader from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def record(**overrides):
    """A minimal, well-formed trail: one lost dispatch, then nothing else."""
    base = {
        "asOf": "2026-10-28T12:10:00Z",
        "contractId": CONTRACT,
        "operation": {"action": "create-order", "argsDigest": DIGEST,
                      "id": "op-1", "idempotencyKey": "order-7f3a"},
        "effectOwner": {"deduplicatesByKey": False, "id": "orders-service",
                        "keyRetentionSeconds": None},
        "attempts": [{"attempt": 1, "argsDigest": DIGEST,
                      "dispatchedAt": "2026-10-28T12:00:01Z",
                      "idempotencyKey": "order-7f3a", "reply": "timeout"}],
        "reconciliations": [],
        "ownerEffects": 1,
        "reported": {"outcome": "unknown"},
    }
    base.update(overrides)
    return base


def add_retry(rec, key="order-7f3a"):
    rec["attempts"].append({"attempt": 2, "argsDigest": DIGEST,
                            "dispatchedAt": "2026-10-28T12:00:40Z",
                            "idempotencyKey": key, "reply": "confirmed"})
    rec["reported"] = {"outcome": "committed"}
    return rec


def probes() -> list[tuple[str, dict, str, str]]:
    """(name, record, expected decision, expected reason)."""
    out: list[tuple[str, dict, str, str]] = []

    # 1. A report that is absent, null or empty cannot be the settled outcome.
    for label, mutate in (
        ("report absent", lambda r: r.pop("reported")),
        ("report null", lambda r: r.__setitem__("reported", {"outcome": None})),
        ("report empty", lambda r: r.__setitem__("reported", {})),
    ):
        rec = record()
        mutate(rec)
        out.append((label, rec, "deny", "outcome_disagrees_with_authority"))
    out.append(("control: report says unknown", record(), "allow", "effect_held"))

    # 2. A ledger count that is not a count is not "at most one".
    for label, value in (("count as string", "1"), ("count as true", True),
                         ("count as float", 1.0), ("count null", None)):
        out.append((label, record(ownerEffects=value), "deny", "duplicate_effect"))
    absent_count = record()
    if "ownerEffects" not in absent_count:
        raise SystemExit("record() no longer builds the key the probe removes")
    absent_count.pop("ownerEffects")
    out.append(("count key absent", absent_count, "deny", "duplicate_effect"))
    out.append(("control: count 1", record(ownerEffects=1), "allow", "effect_held"))
    out.append(("control: count 0", record(ownerEffects=0), "allow", "effect_held"))

    # 3. An owner answer about another key is not a statement about this effect:
    #    it cannot cover a resend, and `settled()` ignores it. A confirmed
    #    dispatch is deliberately different (it settles committed whatever key it
    #    carries, which is what the corpus's retained declaration says for both
    #    cases that build the shape), so these probes are about answers only.
    wrong = add_retry(record(ownerEffects=1))
    wrong["reconciliations"] = [{"at": "2026-10-28T12:00:20Z",
                                 "idempotencyKey": "SOMEONE-ELSES-KEY",
                                 "result": "absent", "final": True}]
    out.append(("cover from another key", wrong, "deny", "retry_after_unknown"))
    right = copy.deepcopy(wrong)
    right["reconciliations"][0]["idempotencyKey"] = "order-7f3a"
    out.append(("control: cover from this key", right, "allow", "effect_held"))

    # The dispatch half of that asymmetry is deliberate and pinned here: a
    # confirmed dispatch settles committed whatever key it carries, which is what
    # the corpus's retained declaration records for identity-missing-key and
    # retry-under-new-key. A reader that "fixes" this will fail this probe, and
    # the failure is the prompt to re-read that declaration rather than the code.
    off_key = record(ownerEffects=1)
    off_key["attempts"] = [{"attempt": 1, "argsDigest": DIGEST,
                            "dispatchedAt": "2026-10-28T12:00:01Z",
                            "idempotencyKey": "SOMEONE-ELSES-KEY", "reply": "confirmed"}]
    off_key["reported"] = {"outcome": "committed"}
    out.append(("dispatches settle by the corpus declaration", off_key, "allow", "effect_held"))

    # The same rule cannot make a record pass on unattributable evidence: with
    # no operation key the identity check refuses before any settling happens.
    keyless = record()
    keyless["operation"] = {k: v for k, v in keyless["operation"].items() if k != "idempotencyKey"}
    out.append(("operation names no key", keyless, "deny", "operation_identity_missing"))

    # 4. A dispatch claiming a lease with no lease record is unverifiable, and
    #    an explicit null claims nothing.
    claimed = record()
    claimed["attempts"][0]["worker"] = "w1"
    claimed["attempts"][0]["fencingToken"] = 9
    out.append(("lease claimed, no lease record", claimed, "deny", "dispatch_beyond_lease"))
    nulled = record()
    nulled["attempts"][0]["worker"] = None
    nulled["attempts"][0]["fencingToken"] = None
    out.append(("control: null lease fields claim nothing", nulled, "allow", "effect_held"))
    out.append(("control: no lease claimed", record(), "allow", "effect_held"))

    # 5. A triggered attempt carries the operation's own key. The corpus builds
    #    three faults from a key spelled after the delivery, but no case where
    #    the trigger itself is absent or unreadable, so those are pinned here.
    def triggered(rec, key="order-7f3a", trigger={"id": "task-2", "kind": "redelivery"}):
        rec["effectOwner"]["deduplicatesByKey"] = True
        rec["attempts"].append({"attempt": 2, "argsDigest": DIGEST,
                                 "dispatchedAt": "2026-10-28T12:00:40Z",
                                 "idempotencyKey": key, "reply": "confirmed",
                                 "trigger": copy.deepcopy(trigger)})
        rec["reported"] = {"outcome": "committed"}
        return rec

    out.append(("control: trigger carries this key", triggered(record()), "allow", "effect_held"))
    out.append(("trigger derives its own key", triggered(record(), key="order-7f3b-task-2"),
                "deny", "idempotency_key_follows_trigger"))
    out.append(("trigger is not an object", triggered(record(), trigger="redelivery"),
                "deny", "idempotency_key_follows_trigger"))
    out.append(("trigger names an unknown kind", triggered(record(), trigger={"id": "x", "kind": "timer"}),
                "deny", "idempotency_key_follows_trigger"))
    out.append(("a first dispatch carries no trigger", record(), "allow", "effect_held"))

    # 8. The ledger cannot hold more effects than the trail has attempts that
    #    could have produced one. The corpus builds two shapes of this; the
    #    no-effect boundary is pinned here because it decides the count.
    orphan = record(ownerEffects=1)
    orphan["attempts"] = []
    orphan["reported"] = {"outcome": "not_run"}
    out.append(("effect with no attempt to account for it", orphan, "deny", "owner_effect_unaccounted"))
    no_effect = record(ownerEffects=1)
    no_effect["attempts"] = [{"attempt": 1, "argsDigest": DIGEST,
                               "dispatchedAt": "2026-10-28T12:00:01Z",
                               "idempotencyKey": "order-7f3a", "reply": "no_effect"}]
    no_effect["reported"] = {"outcome": "not_run"}
    out.append(("a no-effect reply cannot account for an effect", no_effect,
                "deny", "owner_effect_unaccounted"))
    two = record(ownerEffects=2)
    two["effectOwner"]["deduplicatesByKey"] = True
    two["attempts"].append({"attempt": 2, "argsDigest": DIGEST,
                             "dispatchedAt": "2026-10-28T12:00:40Z",
                             "idempotencyKey": "order-7f3a", "reply": "confirmed"})
    two["reported"] = {"outcome": "committed"}
    out.append(("two effects, two attempts, names the count", two, "deny", "duplicate_effect"))

    # 13 to 15. The stop block. The corpus builds one case per fault and one
    #    control per fault; the boundaries either side of the acknowledgement
    #    are pinned here, because a stop read with >= where it should be > turns
    #    a control into a fault and the corpus alone cannot tell the two apart.
    def stopped(rec, **overrides):
        stop = {"requestedAt": "2026-10-28T12:00:09Z",
                "revokedAt": "2026-10-28T12:00:12Z",
                "acknowledgedAt": "2026-10-28T12:00:10Z",
                "authorityChecks": [{"at": "2026-10-28T12:00:20Z", "result": "refused"}],
                "ownerCommit": {"notBefore": "2026-10-28T12:00:02Z",
                                 "notAfter": "2026-10-28T12:00:03Z"},
                "reported": "quiescent"}
        stop.update(overrides)
        rec["stop"] = stop
        return rec

    out.append(("control: clean stop, authority refused", stopped(record()), "allow", "effect_held"))
    out.append(("authority accepted at the revocation",
                stopped(record(), authorityChecks=[{"at": "2026-10-28T12:00:12Z",
                                                    "result": "accepted"}]),
                "deny", "authority_used_after_revocation"))
    out.append(("authority accepted before the revocation",
                stopped(record(), authorityChecks=[{"at": "2026-10-28T12:00:11Z",
                                                    "result": "accepted"}]),
                "allow", "effect_held"))
    out.append(("authority answer unreadable after the revocation",
                stopped(record(), authorityChecks=[{"at": "2026-10-28T12:00:14Z",
                                                    "result": "granted"}]),
                "deny", "authority_used_after_revocation"))
    out.append(("authority check with no instant",
                stopped(record(), authorityChecks=[{"result": "accepted"}]),
                "deny", "authority_used_after_revocation"))

    out.append(("commit wholly after the acknowledgement",
                stopped(record(), ownerCommit={"notBefore": "2026-10-28T12:00:15Z",
                                               "notAfter": "2026-10-28T12:00:16Z"}),
                "deny", "effect_after_stop_reported_quiescent"))
    out.append(("control: same commit, reported after the stop",
                stopped(record(), reported="effect_after_stop",
                        ownerCommit={"notBefore": "2026-10-28T12:00:15Z",
                                     "notAfter": "2026-10-28T12:00:16Z"}),
                "allow", "effect_held"))
    out.append(("commit begins at the acknowledgement",
                stopped(record(), ownerCommit={"notBefore": "2026-10-28T12:00:10Z",
                                               "notAfter": "2026-10-28T12:00:12Z"}),
                "deny", "stop_straddle_reported_settled"))
    out.append(("commit ends at the acknowledgement",
                stopped(record(), ownerCommit={"notBefore": "2026-10-28T12:00:08Z",
                                               "notAfter": "2026-10-28T12:00:10Z"}),
                "deny", "stop_straddle_reported_settled"))
    out.append(("control: straddle reported not established",
                stopped(record(), reported="not_established",
                        ownerCommit={"notBefore": "2026-10-28T12:00:08Z",
                                     "notAfter": "2026-10-28T12:00:12Z"}),
                "allow", "effect_held"))
    out.append(("control: commit before the acknowledgement",
                stopped(record(), ownerCommit={"notBefore": "2026-10-28T12:00:02Z",
                                               "notAfter": "2026-10-28T12:00:03Z"}),
                "allow", "effect_held"))
    out.append(("control: stop states no commit window",
                stopped(record(), ownerCommit=None), "allow", "effect_held"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader", type=Path, default=Path(__file__).with_name("algovoi_uncertain_effect.py"))
    args = parser.parse_args()
    reader = load_reader(args.reader)

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, rec, want_decision, want_reason in probes():
            path = Path(tmp) / "record.json"
            path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
            answer = reader.judge(reader.read(path))
            ok = answer == {"decision": want_decision, "reason": want_reason}
            print(f"{'ok  ' if ok else 'FAIL'} {name:32} {answer['decision']}/{answer['reason']}"
                  f"{'' if ok else f'  want {want_decision}/{want_reason}'}")
            failures += 0 if ok else 1
    print(f"{failures} probes did not hold")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

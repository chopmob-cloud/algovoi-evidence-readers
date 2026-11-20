"""AlgoVoi reader for the uncertain-effect/v1 record format.

This is a second implementation of the checks the corpus
`vectors-uncertain-effect` states, written to be run as an external verifier
under the contract those records publish:

    <verifier> <case-dir>/record.json --json

prints one JSON object carrying `decision` and `reason`, and exits 0 for
`allow` and 1 for `deny`.

It was written from the corpus README and the contract prose, not from the
reference reader's code, but its author had read that code before writing it.
It is therefore a *directed* implementation: its agreement with the corpus is
evidence that the stated checks are implementable by a second pair of hands,
not that the text is determinate from a cold start.

The corpus grew from ten ordered checks over 27 cases to fifteen over 54 between
revision c588c8a and revision 6ba08cb, adding the trigger-key, ledger-accounting
and stop checks. The checks the first version carried were reordered to the
corpus's own order and the five new ones were added from the README text, so the
figure now reported is the one this reader earns at the current revision, not the
earlier one carried forward.

What it deliberately does not do:

  * it never reads the corpus manifest or any expected-result file, so it
    cannot agree with the corpus by looking up the answer;
  * it adds no check the corpus does not state, and no reason outside the
    fifteen the corpus names as the first-failing check;
  * it does not model a signed receipt, an approved request or an observed
    effect, because a trail record carries none of them. The three axes it does
    report are read off the ledger count, the settled outcome and the report,
    which is what checks 8 to 12 turn on (idempotency, completion, outcome
    authority). See README.md for the mapping and for what that means about the
    figure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

SUITE = "uncertain-effect/v1"
CONTRACT_ID = "https://probityai.github.io/agent-evidence-vectors/predicate/v1/uncertain-effect"

ALLOW, DENY = "allow", "deny"
EXIT_FOR = {ALLOW: 0, DENY: 1}
HELD = "effect_held"

# A reply that leaves the caller not knowing whether the effect happened. The
# contract names three: a timeout, a dropped connection and a worker killed
# mid-step. The third is easy to miss and it is the one the crash cases turn on.
LOST_REPLIES = frozenset({"timeout", "connection_lost", "worker_crashed"})
# The outcomes the record format lets a runtime report.
REPORTED_OUTCOMES = ("committed", "not_run", "unknown")
# What a record may say a stop left behind. Anything other than the last of
# these, over an effect that did commit, is a settled claim the interval denies.
STOP_NOT_ESTABLISHED = "not_established"
STOP_QUIESCENT = "quiescent"
# The three events the contract says can set an attempt off. An attempt that
# carries one of these carries the operation's own key.
TRIGGER_KINDS = frozenset({"redelivery", "replay", "completion"})

# The corpus fixes this order: a reader names the first check that fails.
FIRST_FAILING = (
    "contract_id_missing",
    "contract_id_unknown",
    "operation_identity_missing",
    "idempotency_key_args_changed",
    "idempotency_key_follows_trigger",
    "dispatch_beyond_lease",
    "retry_after_unknown",
    "owner_effect_unaccounted",
    "duplicate_effect",
    "unknown_released",
    "outcome_settled_without_authority",
    "outcome_disagrees_with_authority",
    "authority_used_after_revocation",
    "effect_after_stop_reported_quiescent",
    "stop_straddle_reported_settled",
)


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


@dataclass(frozen=True)
class Trail:
    """One record, read once, with the derived facts the checks share."""

    record: dict[str, Any]
    dispatches: tuple[dict[str, Any], ...]
    leases: tuple[dict[str, Any], ...]
    answers: tuple[dict[str, Any], ...]
    owner_effects: Any
    reservation: dict[str, Any] | None
    reported: Any
    stop: dict[str, Any] | None = None

    @property
    def operation(self) -> dict[str, Any]:
        return self.record.get("operation") or {}

    def lost(self, attempt: dict[str, Any]) -> bool:
        return attempt.get("reply") in LOST_REPLIES

    def answer_about_this_effect(self, answer: dict[str, Any]) -> bool:
        """Whether a dispatch or an owner answer speaks about this operation.

        Both carry the idempotency key they belong to, and a record whose
        operation names no key is refused by the identity check before any of
        these callers decides anything, so an unattributable item is never the
        reason a record passes.
        """
        key = self.operation.get("idempotencyKey")
        return key is not None and answer.get("idempotencyKey") == key

    def settled(self) -> str:
        """The outcome the effect owner's own answers settle.

        Committed when any dispatch in the trail was confirmed or one of the
        owner's answers *about this operation* found the effect present.
        Otherwise not_run when nothing was ever lost, or when a final answer
        after the last lost dispatch found nothing. Everything else is unknown:
        the honest reading, never rounded.

        The asymmetry is deliberate and comes from the format, not from taste: a
        confirmed dispatch settles `committed` whatever key it carries, because
        the corpus's own retained declaration says so for both cases that build
        that shape (`identity-missing-key`, `retry-under-new-key`, both
        `committed`), and both are denied on an earlier check anyway. An owner
        answer is different: the format's answers carry the key they were asked
        about, and an answer about another key is not a statement about this
        effect, so it settles nothing here. No corpus case builds an off-key
        answer, which is why this reading is a choice rather than a test result.
        """
        if any(item.get("reply") == "confirmed" for item in self.dispatches):
            return "committed"
        mine = [answer for answer in self.answers if self.answer_about_this_effect(answer)]
        if any(item.get("result") == "present" for item in mine):
            return "committed"
        lost_at = [_stamp(item["dispatchedAt"]) for item in self.dispatches if self.lost(item)]
        if not lost_at:
            return "not_run"
        last_lost = max(lost_at)
        for answer in mine:
            if answer.get("result") == "absent" and answer.get("final") \
                    and _stamp(answer["at"]) >= last_lost:
                return "not_run"
        return "unknown"

    def absent_answer_between(self, start: datetime, end: datetime) -> bool:
        """Whether the owner said, finally, that this effect was not there."""
        return any(answer.get("result") == "absent" and answer.get("final")
                   and self.answer_about_this_effect(answer)
                   and start <= _stamp(answer["at"]) <= end
                   for answer in self.answers)


def read(path: Path) -> Trail:
    record = json.loads(path.read_text(encoding="utf-8"))
    dispatches = tuple(sorted(record.get("attempts") or [],
                              key=lambda item: _stamp(item["dispatchedAt"])))
    return Trail(
        record=record,
        dispatches=dispatches,
        leases=tuple(record.get("leases") or ()),
        answers=tuple(record.get("reconciliations") or ()),
        owner_effects=record.get("ownerEffects"),
        reservation=record.get("reservation"),
        reported=(record.get("reported") or {}),
        stop=record.get("stop"),
    )


# Each check is a predicate over the trail. The mapping is the contract's own:
# the first predicate that holds names the reason.


def contract_missing(trail: Trail) -> bool:
    return "contractId" not in trail.record


def contract_unknown(trail: Trail) -> bool:
    return trail.record.get("contractId") != CONTRACT_ID


def identity_incomplete(trail: Trail) -> bool:
    subjects = (trail.operation, *trail.dispatches)
    return any(not subject.get(field) for subject in subjects
               for field in ("idempotencyKey", "argsDigest"))


def key_reused_with_other_arguments(trail: Trail) -> bool:
    key = trail.operation.get("idempotencyKey")
    digest = trail.operation.get("argsDigest")
    return any(item.get("idempotencyKey") == key and item.get("argsDigest") != digest
               for item in trail.dispatches)


def key_follows_trigger(trail: Trail) -> bool:
    """Whether a triggered attempt carried a key of its own.

    The check reads only what the record states: an attempt that some other
    event set off, a redelivery, a replay or a completion, carries the
    operation's own key. A key spelled after the delivery or task identifier is
    the fault the corpus names, and an attempt that reuses a key derived from
    the trigger makes one operation look like several to an owner that
    deduplicates. An attempt with no trigger is the operation's first dispatch
    or a plain retry and is left to the earlier and later checks.

    A trigger that is present but unreadable cannot be shown to carry the
    operation's key, so it fails here rather than passing on a shape the reader
    could not parse.
    """
    key = trail.operation.get("idempotencyKey")
    for item in trail.dispatches:
        trigger = item.get("trigger")
        if trigger is None:
            continue
        if not isinstance(trigger, dict) or trigger.get("kind") not in TRIGGER_KINDS:
            return True
        if item.get("idempotencyKey") != key:
            return True
    return False


def _inside_a_lease(trail: Trail, attempt: dict[str, Any]) -> bool:
    """Whether this dispatch was inside the lease its worker held at that moment."""
    sent = _stamp(attempt["dispatchedAt"])
    held = [lease for lease in trail.leases
            if lease.get("worker") == attempt.get("worker")
            and lease.get("fencingToken") == attempt.get("fencingToken")]
    if not held:
        return False
    lease = held[0]
    if not _stamp(lease["acquiredAt"]) <= sent < _stamp(lease["expiresAt"]):
        return False
    # A lease with a higher token granted before the send takes the right to
    # dispatch away from the holder, even inside its own window.
    return not any(other.get("fencingToken", -1) > lease["fencingToken"]
                   and _stamp(other["acquiredAt"]) <= sent
                   for other in trail.leases)


def dispatched_beyond_lease(trail: Trail) -> bool:
    """Whether a dispatch happened outside a lease, or claims one that is unrecorded.

    With no lease record at all, the check is satisfied only when no dispatch
    claims to have been sent under one. An attempt carrying a worker or a
    fencing token with nothing to check it against is unverifiable, not
    compliant, so it fails here rather than passing on an absent array.
    """
    if not trail.leases:
        return any(item.get("worker") is not None or item.get("fencingToken") is not None
                   for item in trail.dispatches)
    return any(not _inside_a_lease(trail, item) for item in trail.dispatches)


def retried_after_a_lost_reply(trail: Trail) -> bool:
    """Whether any later dispatch followed a lost one without cover.

    Cover is either an owner that deduplicates the same key inside its window,
    or a final answer in between saying the effect was not there.
    """
    owner = trail.record.get("effectOwner") or {}
    retention = owner.get("keyRetentionSeconds")
    for index, earlier in enumerate(trail.dispatches):
        if not trail.lost(earlier):
            continue
        sent = _stamp(earlier["dispatchedAt"])
        for later in trail.dispatches[index + 1:]:
            resent = _stamp(later["dispatchedAt"])
            same_key = later.get("idempotencyKey") == earlier.get("idempotencyKey")
            within = retention is None or (resent - sent).total_seconds() <= retention
            if owner.get("deduplicatesByKey") and same_key and within:
                continue
            if trail.absent_answer_between(sent, resent):
                continue
            return True
    return False


def owner_effect_unaccounted(trail: Trail) -> bool:
    """Whether the owner's ledger holds effects the trail cannot account for.

    The corpus states the check as a comparison: no more effects than the trail
    has attempts that could have produced one. An attempt the owner answered
    with a definite no-effect could not have produced one, so it is not counted;
    every other attempt could, including one whose worker was killed and one
    whose history was reset, which is why a trail that records no attempt beside
    a single effect fails here and not on the duplicate check.

    A ledger count the reader cannot read as a count is left to the duplicate
    check, which names it, rather than being reported twice.
    """
    count = trail.owner_effects
    if isinstance(count, bool) or not isinstance(count, int):
        return False
    could = sum(1 for item in trail.dispatches if item.get("reply") != "no_effect")
    return count > could


def more_than_one_effect(trail: Trail) -> bool:
    """Whether the owner's ledger holds more than one effect.

    A count that is not a count fails here too: `true` is not one effect, and a
    ledger figure the reader cannot read is not something to pass. The corpus's
    reason vocabulary has no malformed-record value, so the closest one names
    the ledger count it could not accept.
    """
    count = trail.owner_effects
    if isinstance(count, bool) or not isinstance(count, int):
        return True
    return count > 1


def charge_dropped_while_unknown(trail: Trail) -> bool:
    return (trail.reservation is not None
            and trail.settled() == "unknown"
            and not trail.reservation.get("charged"))


def unknown_reported_as_settled(trail: Trail) -> bool:
    return (trail.settled() == "unknown"
            and trail.reported.get("outcome") in ("committed", "not_run"))


def reported_outcome_disagrees(trail: Trail) -> bool:
    """Whether the report is the outcome the owner's answers settle.

    A missing or unrecognised report cannot be that outcome. Without this the
    check passes on a record that reports nothing, which is the one shape a
    reader exists to refuse.
    """
    reported = trail.reported.get("outcome")
    if reported not in REPORTED_OUTCOMES:
        return True
    settled = trail.settled()
    return settled != "unknown" and reported != settled


def _stop(trail: Trail) -> dict[str, Any] | None:
    return trail.stop if isinstance(trail.stop, dict) else None


def _commit_interval(trail: Trail) -> tuple[datetime, datetime] | None:
    """The window the owner's commit fell in, when the stop block states one.

    The corpus states the window as `notBefore` and `notAfter`, the earliest and
    latest instant the effect could have been written, so an interval rather
    than a point. A block missing either end gives no interval, and the two
    checks that read one stay silent rather than guessing.
    """
    stop = _stop(trail)
    if stop is None:
        return None
    commit = stop.get("ownerCommit") or {}
    if not isinstance(commit, dict) or not commit.get("notBefore") or not commit.get("notAfter"):
        return None
    return _stamp(commit["notBefore"]), _stamp(commit["notAfter"])


def _acknowledged(trail: Trail) -> datetime | None:
    stop = _stop(trail)
    if stop is None or not stop.get("acknowledgedAt"):
        return None
    return _stamp(stop["acknowledgedAt"])


def authority_used_after_revocation(trail: Trail) -> bool:
    """Whether an authority check was accepted at or after the revocation.

    A stop that has been acknowledged revokes the authority to act. A check made
    at or after `revokedAt` and accepted is the record using authority the stop
    already withdrew. Only a refusal is read as a refusal: an answer the reader
    does not recognise is not evidence that the authority was withheld, so it
    counts against the record rather than for it.
    """
    stop = _stop(trail)
    if stop is None or not stop.get("revokedAt"):
        return False
    revoked = _stamp(stop["revokedAt"])
    for check in stop.get("authorityChecks") or ():
        if not isinstance(check, dict) or not check.get("at"):
            # A check the reader cannot place in time cannot be shown to have
            # come before the revocation, so it counts against the record.
            return True
        if _stamp(check["at"]) < revoked:
            continue
        if check.get("result") != "refused":
            return True
    return False


def effect_after_stop_reported_quiescent(trail: Trail) -> bool:
    """Whether an effect committed after the stop was reported as a clean stop.

    The contract's word is wholly: the earliest instant the owner could have
    written the effect is after the acknowledgement. An effect in that window is
    real and the stop did not prevent it, so a record that calls the stop
    quiescent contradicts its own commit block.
    """
    stop = _stop(trail)
    interval = _commit_interval(trail)
    acknowledged = _acknowledged(trail)
    if stop is None or interval is None or acknowledged is None:
        return False
    return interval[0] > acknowledged and stop.get("reported") == STOP_QUIESCENT


def stop_straddle_reported_settled(trail: Trail) -> bool:
    """Whether an effect straddling the stop was reported as anything but open.

    The acknowledgement falls inside the window the owner could have committed
    in, so the record cannot say whether the stop came first. The only honest
    report is that the effect is not established, and every other answer, a
    quiescent stop, an effect after the stop, or no report the reader can read,
    claims a settled state the interval does not support.
    """
    stop = _stop(trail)
    interval = _commit_interval(trail)
    acknowledged = _acknowledged(trail)
    if stop is None or interval is None or acknowledged is None:
        return False
    if not interval[0] <= acknowledged <= interval[1]:
        return False
    return stop.get("reported") != STOP_NOT_ESTABLISHED


CHECKS: dict[str, Callable[[Trail], bool]] = {
    "contract_id_missing": contract_missing,
    "contract_id_unknown": contract_unknown,
    "operation_identity_missing": identity_incomplete,
    "idempotency_key_args_changed": key_reused_with_other_arguments,
    "idempotency_key_follows_trigger": key_follows_trigger,
    "dispatch_beyond_lease": dispatched_beyond_lease,
    "retry_after_unknown": retried_after_a_lost_reply,
    "owner_effect_unaccounted": owner_effect_unaccounted,
    "duplicate_effect": more_than_one_effect,
    "unknown_released": charge_dropped_while_unknown,
    "outcome_settled_without_authority": unknown_reported_as_settled,
    "outcome_disagrees_with_authority": reported_outcome_disagrees,
    "authority_used_after_revocation": authority_used_after_revocation,
    "effect_after_stop_reported_quiescent": effect_after_stop_reported_quiescent,
    "stop_straddle_reported_settled": stop_straddle_reported_settled,
}


def judge(trail: Trail) -> dict[str, str]:
    for name in FIRST_FAILING:
        if CHECKS[name](trail):
            return {"decision": DENY, "reason": name}
    return {"decision": ALLOW, "reason": HELD}


def native(trail: Trail) -> dict[str, Any]:
    """The axes of the AlgoVoi offline verifier this record can speak to.

    Reported beside the decision, never instead of it. The verifier's signing,
    request-binding and effect-binding axes are absent here: a trail record has
    no receipt and no observed effect to bind.
    """
    settled = trail.settled()
    reported = trail.reported.get("outcome")
    multiplicity = "PASS" if trail.owner_effects == 1 else "FAIL"
    # The verifier's completion axis is tri-state: a realised effect that was
    # acknowledged passes, the same effect without its acknowledgement stays
    # UNCONFIRMED, and an effect that never happened is not something it
    # certifies at all.
    completion = {"committed": "PASS", "unknown": "UNCONFIRMED", "not_run": "FAIL"}[settled]
    return {
        "idempotency": {"result": multiplicity, "detail": f"owner ledger holds {trail.owner_effects}"},
        "completion": {"result": completion, "detail": f"settled outcome {settled}"},
        "outcome_authority": {
            "result": "PASS" if not (settled == "unknown" and reported != "unknown")
                      and reported == settled else "FAIL",
            "detail": f"reported {reported}, settled {settled}",
        },
        "not_exercised": ["receipt_signature", "request_binding", "effect_binding", "amount_consistency"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlgoVoi reader for uncertain-effect/v1 records")
    parser.add_argument("record", help="path to one case's record.json")
    parser.add_argument("--json", action="store_true", help="print the decision as one JSON object")
    parser.add_argument("--native", action="store_true",
                        help="also print the AlgoVoi axis view to stderr")
    args = parser.parse_args(argv)
    trail = read(Path(args.record))
    answer = judge(trail)
    if args.native:
        print(json.dumps({"record": Path(args.record).parent.name, "axes": native(trail)}, sort_keys=True),
              file=sys.stderr)
    print(json.dumps(answer, sort_keys=True))
    return EXIT_FOR[answer["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())

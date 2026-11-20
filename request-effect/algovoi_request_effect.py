"""An independent reader for the request-effect-join fixture contract.

Written from `docs/INPUT-CONTRACT.md` (`request-effect-join-fixture/v1`) without
reading `run_contract.py`, the reference implementation. Nothing here imports the
contract author's code or any probityai package: the canonicalisation below is
this file's own RFC 8785 implementation, so a digest that recomputes here is a
digest two implementations agree on.

Usage:
    python3 algovoi_request_effect.py CASES.json [--expected EXPECTED.json] [--json]

Exit 0 when every case is answered; with `--expected`, exit 1 on any divergence
and 2 when the case file cannot be read as this contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CONTRACT = "request-effect-join-fixture/v1"
# RFC 8785 serialises numbers as IEEE-754 doubles, so this is the largest integer
# that survives canonicalisation exactly. A conforming JCS implementation refuses
# anything beyond it rather than emitting digits that no double holds.
SAFE_INT_MAX = 2**53 - 1
JCS_ALIAS = "jcs-rfc8785-v1"
JCS_ADDRESS = "f018a62879ab01f21e8fe5e9e7486dadba0b795e9358b32fd24d38d2c1f1f07d"
SUPPORTED_PROFILES = {JCS_ALIAS, JCS_ADDRESS}

TOP_KEYS = {
    "case_id", "contract", "source", "approved", "effective", "decision",
    "covering_decision", "attempt", "result", "later_observation",
}
SOURCE_KEYS = {"producer_vector", "frequency_row", "note"}
REF_PREIMAGE_KEYS = {
    "approved": {"actor_id", "input_hash", "scope", "ts_ms", "version"},
    "effective": {"actor_id", "input_hash", "scope", "ts_ms", "version"},
    "decision": {"action_ref", "canonicalization_profile_id", "outcome",
                 "policy_id", "ts_ms", "version"},
    "covering_decision": {"action_ref", "canonicalization_profile_id", "outcome",
                          "policy_id", "ts_ms", "version"},
    "attempt": {"action_ref", "attempt_seq", "executor_id", "ts_ms", "version"},
    "result": {"attempt_id", "outcome", "output_hash", "ts_ms", "version"},
}
LATER_KEYS = {"target_scope", "content_sha256", "observed_at_ms", "observer_id"}
REF_KEYS = {"ref", "preimage"}

# Findings are a closed set in the contract. Anything not in here is a bug.
FINDINGS = {
    "join_established", "join_not_established", "effective_call_not_covered",
    "covering_decision_not_permit", "digest_mismatch",
    "unsupported_canonical_profile", "join_chain_broken", "result_not_completed",
    "refusal_observed", "store_mutation_not_established",
    "later_state_not_supplied", "later_state_unchanged", "later_state_differs",
    "later_state_not_comparable", "original_result_preserved",
}

CLASSIFICATIONS = {"established", "refused", "not_established", "invalid_input"}


class InvalidInput(Exception):
    """The record is not well formed under this contract."""

    def __init__(self, native: str, findings: tuple[str, ...] = ()) -> None:
        super().__init__(native)
        self.native = native
        self.findings = findings


# --------------------------------------------------------------------------
# RFC 8785 canonicalisation, implemented here rather than imported.
# --------------------------------------------------------------------------

def _utf16_key(text: str) -> bytes:
    """RFC 8785 sorts object keys by their UTF-16 code units, not code points."""
    return text.encode("utf-16-be")


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(value: float | int) -> str:
    """ECMAScript Number::toString, which is what RFC 8785 section 3.2.2.3 pins.

    The boundary is the thing to get right and the thing I had wrong: the
    exponential form starts strictly below 1e-6, so `1e-6` is `0.000001` and
    `1e-7` is `1e-7`. Python's repr uses a different rule and cannot be used
    directly.

    Integers are also bounded by the I-JSON safe range. JCS serialises numbers as
    IEEE-754 doubles, so an integer beyond +/- (2**53 - 1) cannot be represented
    exactly and a conforming implementation refuses it rather than printing its
    decimal digits. Accepting such an integer would make this reader disagree with
    any other JCS implementation on the digest of the same record, so it is
    refused as invalid input here too. The differential test against `rfc8785`
    found this; the corpus cannot, because every `ts_ms` is around 1.8e12.
    """
    if isinstance(value, bool):
        raise InvalidInput("JCS_BOOLEAN_NOT_NUMBER")
    if isinstance(value, int):
        if abs(value) > SAFE_INT_MAX:
            raise InvalidInput("JCS_INTEGER_OUTSIDE_SAFE_RANGE")
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise InvalidInput("JCS_NON_FINITE_NUMBER")
    if value == 0:
        return "0"  # -0.0 and 0.0 both serialise as 0

    negative = value < 0
    sign = "-" if negative else ""

    # The shortest decimal string that round-trips, taken from repr, then
    # expressed as digits and a power of ten.
    text = repr(abs(value))
    if "e" in text or "E" in text:
        mantissa, _, exponent = text.lower().partition("e")
        exponent = int(exponent)
    else:
        mantissa, exponent = text, 0
    if "." in mantissa:
        whole, _, fraction = mantissa.partition(".")
    else:
        whole, fraction = mantissa, ""
    digits = (whole + fraction).lstrip("0")
    # exponent now expresses digits * 10^exponent
    exponent = exponent - len(fraction)
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    digits = stripped or "0"

    k = len(digits)
    n = k + exponent  # position of the decimal point relative to the digits

    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    if k == 1:
        return f"{sign}{digits}e{'+' if e >= 0 else ''}{e}"
    return f"{sign}{digits[0]}.{digits[1:]}e{'+' if e >= 0 else ''}{e}"


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, list):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(f"{_string(k)}:{_canonical(v)}" for k, v in items) + "}"
    raise InvalidInput("JCS_UNSUPPORTED_TYPE")


def ref_of(preimage: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(preimage).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def _shape(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise InvalidInput("RECORD_NOT_OBJECT")

    keys = set(record)
    if keys != TOP_KEYS:
        raise InvalidInput("RECORD_KEY_SET")
    if record.get("contract") != CONTRACT:
        raise InvalidInput("CONTRACT_ID")

    case_id = record.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise InvalidInput("CASE_ID_NOT_STRING")

    source = record.get("source")
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise InvalidInput("SOURCE_NOT_DECLARED_SHAPE")

    for field in ("approved", "effective", "decision"):
        _shape_ref(record.get(field), field, required=True)
    _shape_ref(record.get("covering_decision"), "covering_decision", required=False)

    attempt = record.get("attempt")
    result = record.get("result")
    _shape_ref(attempt, "attempt", required=False)
    _shape_ref(result, "result", required=False)

    # attempt and result agree about being present; one without the other is invalid.
    # A covering decision may legitimately exist on a record where nothing was
    # dispatched, so it is NOT constrained here: the contract's order of checks
    # reaches dispatch (step 4) before coverage (step 5), and refusing that shape
    # as malformed would answer `invalid_input` where the contract says
    # `not_established`.
    if (attempt is None) != (result is None):
        raise InvalidInput("ATTEMPT_RESULT_PRESENCE")

    # later_observation: an object, or null. ABSENT is invalid, and is caught by
    # the key-set check above. This is the S-05 / S-06 seam.
    later = record.get("later_observation")
    if later is not None:
        if not isinstance(later, dict) or set(later) != LATER_KEYS:
            raise InvalidInput("LATER_OBSERVATION_NOT_DECLARED_SHAPE")
        if not isinstance(later.get("target_scope"), str):
            raise InvalidInput("LATER_SCOPE_NOT_STRING")
        if not isinstance(later.get("content_sha256"), str):
            raise InvalidInput("LATER_CONTENT_NOT_STRING")
        observed = later.get("observed_at_ms")
        if observed is not None and (isinstance(observed, bool) or not isinstance(observed, int)):
            raise InvalidInput("LATER_OBSERVED_AT_NOT_INTEGER")
        if not isinstance(later.get("observer_id"), str):
            raise InvalidInput("LATER_OBSERVER_NOT_STRING")

    return record


def _shape_ref(value: Any, field: str, *, required: bool) -> None:
    if value is None:
        if required:
            raise InvalidInput(f"{field.upper()}_IS_NULL")
        return
    if not isinstance(value, dict) or set(value) != REF_KEYS:
        raise InvalidInput(f"{field.upper()}_NOT_DECLARED_SHAPE")
    ref = value.get("ref")
    if not isinstance(ref, str) or not ref.startswith("sha256:"):
        raise InvalidInput(f"{field.upper()}_REF_NOT_SHA256")
    preimage = value.get("preimage")
    if not isinstance(preimage, dict):
        raise InvalidInput(f"{field.upper()}_PREIMAGE_NOT_OBJECT")
    expected = REF_PREIMAGE_KEYS[field]
    if set(preimage) != expected:
        raise InvalidInput(f"{field.upper()}_PREIMAGE_KEY_SET")
    # Types, not just presence. `True` is an int in Python and a boolean in JSON,
    # and a reader that lets it stand has stopped reading the declared shape.
    def _is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    for key, value in preimage.items():
        if key in ("ts_ms", "attempt_seq"):
            if not _is_int(value):
                raise InvalidInput(f"{field.upper()}_{key.upper()}_NOT_INTEGER")
        else:
            if not isinstance(value, str):
                raise InvalidInput(f"{field.upper()}_{key.upper()}_NOT_STRING")
    if preimage.get("version") != "request-effect-fixture-v1":
        raise InvalidInput(f"{field.upper()}_PREIMAGE_VERSION")


# --------------------------------------------------------------------------
# The ordered checks
# --------------------------------------------------------------------------

def evaluate(record: Any) -> dict[str, Any]:
    """Classify one record. Returns classification, findings and a native code."""
    try:
        rec = _shape(record)
    except InvalidInput as exc:
        return _verdict("invalid_input", (), exc.native)

    # 2. profile — every supplied decision, including the covering one.
    decisions = [rec["decision"]]
    if rec["covering_decision"] is not None:
        decisions.append(rec["covering_decision"])
    for decision in decisions:
        profile = decision["preimage"]["canonicalization_profile_id"]
        if profile not in SUPPORTED_PROFILES:
            return _verdict("invalid_input", ("unsupported_canonical_profile",),
                            "CANONICAL_PROFILE_UNSUPPORTED")

    # 3. digest — every supplied ref recomputes. A failure stops the run.
    supplied = [("approved", rec["approved"]), ("effective", rec["effective"]),
                ("decision", rec["decision"])]
    if rec["covering_decision"] is not None:
        supplied.append(("covering_decision", rec["covering_decision"]))
    if rec["attempt"] is not None:
        supplied.append(("attempt", rec["attempt"]))
        supplied.append(("result", rec["result"]))
    for name, obj in supplied:
        try:
            recomputed = ref_of(obj["preimage"])
        except InvalidInput as exc:
            return _verdict("invalid_input", (), exc.native)
        if obj["ref"] != recomputed:
            return _verdict("refused", ("digest_mismatch",),
                            f"DIGEST_MISMATCH_{name.upper()}")

    # 4. dispatch — nothing was dispatched settles neither way.
    if rec["attempt"] is None:
        findings = ["join_not_established", "store_mutation_not_established"]
        if rec["decision"]["preimage"]["outcome"] != "permit":
            findings.append("refusal_observed")
        return _verdict("not_established", tuple(findings), "NO_DISPATCH")

    # 5. coverage — a rewritten call needs a decision that names it AND permits it.
    if rec["effective"]["ref"] != rec["approved"]["ref"]:
        covering = rec["covering_decision"]
        effective_ref = rec["effective"]["ref"]
        if covering is None or covering["preimage"]["action_ref"] != effective_ref:
            return _verdict("refused", ("effective_call_not_covered",),
                            "REWRITE_NOT_COVERED")
        if covering["preimage"]["outcome"] != "permit":
            return _verdict("refused", ("covering_decision_not_permit",),
                            "COVERING_NOT_PERMIT")

    # 5b. A call the guardrail did not permit cannot be established by the fact that
    #     it ran. The contract lists `established` as "the effective call is bound to
    #     a decision that permits it", and names no finding for this shape, so the
    #     honest answer is a refusal with the nearest declared finding rather than a
    #     pass. This shape does not appear in the corpus; it is asserted by
    #     check_named_probes.py so a later edit cannot quietly restore the pass.
    permitting = rec["decision"]["preimage"]["outcome"] == "permit"
    if rec["covering_decision"] is not None:
        permitting = permitting or (
            rec["covering_decision"]["preimage"]["outcome"] == "permit"
            and rec["covering_decision"]["preimage"]["action_ref"]
            == rec["effective"]["ref"])
    if not permitting:
        return _verdict("refused", ("effective_call_not_covered",),
                        "DISPATCHED_UNDER_REFUSAL")

    # 6. chain — attempt names effective, result names attempt.
    if rec["attempt"]["preimage"]["action_ref"] != rec["effective"]["ref"]:
        return _verdict("refused", ("join_chain_broken",), "ATTEMPT_NAMES_OTHER_CALL")
    if rec["result"]["preimage"]["attempt_id"] != rec["attempt"]["ref"]:
        return _verdict("refused", ("join_chain_broken",), "RESULT_NAMES_OTHER_ATTEMPT")
    if rec["result"]["preimage"]["outcome"] != "completed":
        return _verdict("not_established", ("result_not_completed",),
                        "RESULT_NOT_COMPLETED")

    # 7. later state — only for an established join.
    later = rec["later_observation"]
    if later is None:
        return _verdict("established",
                        ("join_established", "later_state_not_supplied"),
                        "JOIN_ESTABLISHED_NO_LATER_READ")
    if later["target_scope"] != rec["approved"]["preimage"]["scope"]:
        return _verdict("established",
                        ("join_established", "later_state_not_comparable"),
                        "LATER_READ_OTHER_SCOPE")
    if later["content_sha256"] == rec["result"]["preimage"]["output_hash"]:
        return _verdict("established",
                        ("join_established", "later_state_unchanged"),
                        "LATER_READ_UNCHANGED")
    return _verdict("established",
                    ("join_established", "later_state_differs",
                     "original_result_preserved"),
                    "LATER_READ_DIFFERS")


def _verdict(classification: str, findings: tuple[str, ...], native: str) -> dict[str, Any]:
    if classification not in CLASSIFICATIONS:
        raise AssertionError(f"classification outside the closed set: {classification}")
    unknown = set(findings) - FINDINGS
    if unknown:
        raise AssertionError(f"finding outside the closed set: {sorted(unknown)}")
    return {"classification": classification, "findings": list(findings),
            "native": native}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__.strip().splitlines()[-3].strip(), file=sys.stderr)
        return 2

    cases_path = Path(args[0])
    try:
        payload = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {cases_path}: {exc}", file=sys.stderr)
        return 2
    records = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        print("case file carries no list of cases", file=sys.stderr)
        return 2

    expected = None
    if "--expected" in flags:
        position = argv.index("--expected")
        if position + 1 >= len(argv) or argv[position + 1].startswith("--"):
            print("--expected needs a path to an expected.json", file=sys.stderr)
            return 2
        path = argv[position + 1]
        try:
            expected = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 2

    results = []
    for record in records:
        verdict = evaluate(record)
        case_id = record.get("case_id") if isinstance(record, dict) else "?"
        results.append({"case_id": case_id, **verdict})

    if "--json" in flags:
        print(json.dumps(results, indent=2, sort_keys=True))
        if expected is not None:
            report = compare(results, expected)
            if report:
                for line in report:
                    print("DIVERGENCE " + line, file=sys.stderr)
                return 1
        return 0

    for r in results:
        print(f"{r['case_id']:6} {r['classification']:16} "
              f"{' '.join(r['findings']) or '-'}")

    if expected is None:
        return 0

    divergences = compare(results, expected)
    if divergences:
        print(f"\n{len(divergences)} divergence(s) against expected.json")
        for line in divergences:
            print("  " + line)
        return 1
    print(f"\n{len(results)} of {len(records)} cases match expected.json")
    return 0


def compare(results: list[dict[str, Any]], expected: dict[str, Any]) -> list[str]:
    """Compare classifications, finding sets and must_not_claim per case."""
    declared = {c["case_id"]: c for c in expected["cases"]}
    divergences: list[str] = []
    seen = set()
    for r in results:
        cid = r["case_id"]
        if cid not in declared:
            divergences.append(f"{cid}: not named by expected.json")
            continue
        seen.add(cid)
        want = declared[cid]
        if r["classification"] != want["classification"]:
            divergences.append(f"{cid}: classification {r['classification']} != "
                               f"{want['classification']}")
        got, expect = set(r["findings"]), set(want["findings"])
        for missing in sorted(expect - got):
            divergences.append(f"{cid}: missing finding {missing}")
        for extra in sorted(got - expect):
            divergences.append(f"{cid}: extra finding {extra}")
        for forbidden in sorted(set(want.get("must_not_claim", ())) & got):
            divergences.append(f"{cid}: reported must_not_claim {forbidden}")
    for cid in sorted(set(declared) - seen):
        divergences.append(f"{cid}: expected.json case not present in the run")
    return divergences


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

# A reader for the `request-effect-join` contract

An independent implementation of `request-effect-join-fixture/v1`, written from
`docs/INPUT-CONTRACT.md` in `probityai/probityai.github.io`
(`atlas/examples/request-effect-join/`). It answers the ask in
`probityai/agent-evidence-vectors#352`: a reader that did not write the contract or
the vectors.

```sh
bash run.sh                      # the whole gate, staged at the pinned commit
python3 algovoi_request_effect.py cases.json --expected expected.json
```

Exit 0 when every case matches, 1 on a divergence, 2 when the case file cannot be
read as this contract. `--json` prints machine-readable verdicts with the `native`
code each finding was mapped from.

## What it does

One record in, one of four classifications out, plus findings drawn from the
contract's closed set of fifteen. The seven ordered checks are implemented as the
contract states them: shape, canonicalisation profile, digest, dispatch, coverage,
chain, later state.

It imports nothing from `agent_evidence_vectors` or from the contract's own
`run_contract.py`. The RFC 8785 canonicaliser is written here, so a digest that
recomputes is two implementations agreeing rather than one function called twice.

## Result

Ten of ten cases match `expected.json`, including every `must_not_claim`
prohibition, on CPython 3.12.16 and 3.11.15 in two clean-room rounds each. See
[`RUN-2026-11-01.md`](RUN-2026-11-01.md) for the logs, the pinned digests, and what
the run does not prove.

`run_contract.py`, the contract author's own reader, was not read before this one
was written. That is a claim about process and no log can evidence it; what the
log can evidence is the differential test against `rfc8785`, plus the fact that
this reader disagrees with the contract on one shape (below), which a copy would
not do.

## The gate

| Script | What it establishes |
| --- | --- |
| `run.sh` | Stages at the pinned commit, verifies both manifest digests before reading anything, then runs all four stages. Exits non-zero on any failure, including a stage that could not run. |
| `check_requests_bite.py` | 41 checks. Every ordered check, disabled in turn, must turn the corpus red; every `must_not_claim` finding must have a mutation that produces it. A prohibition with no witness is untested. |
| `check_named_probes.py` | 21 probes with controls, over shapes the corpus never builds: missing `attempt`, null-vs-absent `later_observation`, a covering decision that denies, an attempt naming the approved call, a boolean `attempt_seq`, a dispatched call under a refusal, a covering decision with nothing dispatched, and the two halves of the permission asymmetry. |
| `differential_jcs.py` | The hand-written canonicaliser against `rfc8785` 0.1.4: 51 preimages, 51 re-derived refs, 400 generated values including the UTF-16 key-order and ECMAScript number-spelling cases that split naive implementations. |

## Three things a green run did not catch

All three were found by `check_named_probes.py`, not by the corpus: the first two
by probes written for the shapes the corpus lacks, the third by turning the same
technique on the code the fixes added.

1. **`attempt_seq: true` classified `established`.** `True` is an `int` in Python,
   so a truthiness accident stood in for a declared shape. Preimage fields are now
   type-checked.
2. **A call dispatched under a `deny` decision classified `established`.** The
   reader verified that a decision *bound* the call, never that it *permitted* it,
   outside the rewrite path. It now refuses, native code `DISPATCHED_UNDER_REFUSAL`.
3. **A `covering_decision` beside `attempt: null` was refused as `invalid_input`**,
   on a rule the contract does not state. The contract's order of checks reaches
   dispatch before coverage, so the answer is `not_established`. Found by
   self-testing the strictness added for item 2; no reviewer raised it.

A model gate was also run. It produced no finding that survived checking — five
were refuted with a single command each — while it did catch two deliberately
planted mutants, which is what makes it worth pointing at a diff at all. The
Copilot `CLEAN` verdicts were four output tokens on a 1,400-line diff and are
recorded as non-reviews rather than passes.

## Where this reader disagrees with the contract

Item 2 is a deliberate divergence. `request-effect-join-fixture/v1` defines
`established` as "the effective call is bound to a decision that permits it", but
its closed finding set has no value for "permitted by nothing, yet dispatched", and
its seven ordered checks have no step that reaches the shape. Followed literally,
the steps return `established` for a denied call that ran — which is what this
reader did before the probe existed.

The two coherent fixes are a new finding (`effective_call_not_permitted`) or
applying the coverage check to every record rather than only rewritten ones. Which
one is right belongs to the contract's author; this reader picks the safer answer
in the meantime and says so rather than presenting the choice as settled.

## Limits

- Ten cases are a small corpus. It contains no missing-`attempt` record, no boolean
  integer, and no dispatched-under-refusal record; the probes exist because the
  corpus cannot reach those shapes.
- The run is **directed**: the contract text was read before the reader was
  written. It shows the text is implementable, not that it is determinate from a
  cold start.
- Nothing here bears on any producer's behaviour, any execution, or any store. The
  vectors are synthetic and the contract says so itself.

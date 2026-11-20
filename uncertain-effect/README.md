# AlgoVoi reader for `uncertain-effect/v1`

A second implementation of the fifteen checks the corpus
`corpora/vectors-uncertain-effect` states, run through that corpus's own
external-verifier contract:

```
<verifier> <case-dir>/record.json --json
```

prints one JSON object carrying `decision` and `reason`, and exits 0 for
`allow` and 1 for `deny`.

Result at the pinned inputs below: **54 of 54 cases answered, 0 disagreements**,
with the corpus's reference rail also at 54 of 54, 0 disagreements, in the same
run. The full record, including the mutation matrix that shows each check can
fail, is in [RUN-2026-11-20.md](RUN-2026-11-20.md). The earlier record,
[RUN-2026-11-01.md](RUN-2026-11-01.md), is the figure at revision `c588c8a`,
where the corpus had 27 cases and ten checks; it is kept because it is still true
of that revision, not because it describes this one.

## Why this exists

The corpus makes a claim the AlgoVoi offline verifier also makes in a narrower
place: a lost reply leaves the effect unknown, and a reader that rounds it to
success or to failure is wrong. The corpus states that claim as fifteen ordered
checks over an attempt trail. Our verifier states it over three records, one of
which is a signed receipt. Checking whether the two readings agree is worth
doing, and the only way to check it is to run a second implementation.

## Is this a run of the AlgoVoi offline verifier?

No, and the difference matters for how the figure should be read.

An `uncertain-effect/v1` record is an attempt trail: operation, dispatches with
the reply each got, leases, the effect owner's reconciliation answers, the
owner's own effect count, an optional reservation, and the outcome the runtime
reported. It carries **no receipt, no approved request and no observed effect**.
The offline verifier's first four axes (`receipt_signature`, `request_binding`,
`effect_binding`, `amount_consistency`) have nothing to read, so this reader
makes no claim about them.

What it does carry over is the part of the verifier's model the trail can speak
to, and `--native` prints it beside the decision:

| AlgoVoi axis | What this reader reads it from | Cases where it fires |
| --- | --- | --- |
| `idempotency` | `ownerEffects` on the owner's ledger | FAIL on 16 of 54, PASS on 38 |
| `completion` | the settled outcome, `committed` / `not_run` / `unknown` | PASS 40, `UNCONFIRMED` 11, FAIL 3 |
| `outcome_authority` | reported outcome against the settled one | PASS 47, FAIL 7 (all seven deny) |

Fifteen of the sixteen `idempotency` failures are records the corpus also denies.
The sixteenth is not a disagreement about the corpus verdict but a difference
between the two questions. `allow-timeout-reconciled-absent-not-run` is allowed by
the corpus, correctly: the owner answered finally that the effect was never
there, so the operation provably never ran. Our axis reads a ledger holding zero
effects as a failure, because the offline verifier certifies realised effects
only. Three of the sixteen hold zero effects. The axis output should be read as
"what our verifier would say about this evidence", never as a second opinion on
the corpus verdict.

## What the corpus cannot see

An outside audit of the first version of this reader found four places where it
failed open and the 27 cases still passed, because the corpus never builds those
shapes. All four are closed, and `check_audit_probes.py` holds each one shut with
a control that must still pass. The same technique was applied to the five checks
added for the current revision, which is where the other twenty probes come from:

| Shape | Before | Now |
| --- | --- | --- |
| `reported` absent, null or empty | allow | deny `outcome_disagrees_with_authority` |
| `ownerEffects` is `"1"`, `true`, `1.0` or absent | `true` allowed; others denied under a miscount | deny `duplicate_effect` |
| cover for a resend from an answer about another key | allow | deny `retry_after_unknown` |
| a dispatch claiming a lease with no lease record | allow | deny `dispatch_beyond_lease` |
| a trigger that is a string, or names a kind outside the three the contract lists | not read at all | deny `idempotency_key_follows_trigger` |
| a no-effect reply beside one effect on the ledger | not read at all | deny `owner_effect_unaccounted` |
| a commit starting or ending exactly at the acknowledgement | not read at all | deny `stop_straddle_reported_settled` |
| a stop block with no commit window | not read at all | read as no claim, and allowed |

The first two were this reader's own defects; the next two are shared with the
corpus's reference reader, so the corpus cannot test for them by construction.
The corpus has no case where a trigger is unreadable, where a ledger count of one
has no attempt behind it, or where a commit boundary lands exactly on the
acknowledgement. Those boundaries are what a stop check gets wrong in either
direction, so they are pinned with a control on each side.

## What it does not establish

- Nothing about a producer's honesty. A record is what someone wrote down.
- Nothing about the effect owner's ledger being correct, or about clocks.
- Nothing about the receipt-binding case. Those four axes are absent, not
  passed.
- Not a blind result. The author had read the reference reader before writing
  this one, so the run is **directed**: it shows the checks are implementable by
  a second pair of hands, not that the text is determinate from a cold start.
- One interpreter on one host. The 1 November record used CPython 3.12 and 3.11
  in disposable Linux containers; the current record did not, so the second
  interpreter and the container replay are outstanding rather than passed.

## Run it

```sh
bash run.sh                  # reference rail, this reader, the axis report, then the audit probes
bash check_reader_bites.sh   # disable each check in turn and require the corpus to go red
python3 check_audit_probes.py # the shapes the corpus never builds, each with its control
```

Both scripts fetch the corpus by commit from codeload and install the harness
from PyPI, so a clean machine reproduces the figure without a checkout of either
repository. `WORK=<dir>` moves the working directory; `COMMIT=` and `HARNESS=`
repin the inputs. The corpus directory is resolved under `corpora/` when it is
there, so a commit from before the corpus moved still replays, and the venv entry
points are found under either `bin/` or `Scripts/`.

Pinned inputs, as reported by `run.sh`:

| | |
| --- | --- |
| Corpus | `probityai/agent-evidence-vectors` at `6ba08cbe9d7f2819e3c47c84adee2fa65390e123`, `corpora/vectors-uncertain-effect` |
| `corpusDigest` | `81e840e8585c20b07bf86394e2d0976f5b7db7801609ca7dc4af3c385d226bca` |
| Harness | `agent-evidence-vectors==0.37.1` from PyPI (`Requires-Python >=3.11`) |
| Reader | `algovoi_uncertain_effect.py`, sha256 `ba3a7b353ca401332fb5dec497acbec43b12f3c0c1676964cb571f917eb145a7` |

## Licence

Apache-2.0, as the rest of this repository. Keep and adapt whatever is useful.

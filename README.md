# AlgoVoi evidence readers

Two readers written by AlgoVoi for conformance corpora published by
[Probity AI](https://github.com/probityai), each run through the corpus's own
external-verifier contract, with the gates that show the checks are load bearing.

Both exist to answer one question that a corpus cannot answer about itself: can
an implementation that did not write the vectors read the specification and get
the same answers. Five rails written by one author catch each other's
transcription errors and share one reading of the specification; they cannot
catch a misreading, because they all inherit it.

| Reader | It reads | Pinned revision | Figure |
| --- | --- | --- | --- |
| [`uncertain-effect/`](uncertain-effect/) | `corpora/vectors-uncertain-effect` in `probityai/agent-evidence-vectors`, 15 ordered checks over 54 cases | `6ba08cbe9d7f2819e3c47c84adee2fa65390e123` | 54 of 54 answered, 0 disagreements, reference rail also 54 of 54 |
| [`request-effect/`](request-effect/) | `request-effect-join-fixture/v1` in `probityai/probityai.github.io`, 4 classifications and a closed set of 15 findings over 11 cases | `808041f552278a9708bd4903860e28fe07ef18d8` | 11 of 11 match `expected.json`, including every `must_not_claim` prohibition |
| [`uncertain-effect-go/`](uncertain-effect-go/) | the same corpus and the same fifteen checks, in Go, built to a static binary | `6ba08cbe9d7f2819e3c47c84adee2fa65390e123` | 54 of 54 answered, 0 disagreements. A port of the Python reader by the same author, so a second language rather than a second author |

Each directory carries its own README, its scripts, and a run record per revision
with the pinned inputs, the logs, and what the figure does not establish.

## Where this comes from

Both readers answer an invitation in
[`probityai/agent-evidence-vectors#352`](https://github.com/probityai/agent-evidence-vectors/issues/352),
which asks projects to bring a case rather than a comment and to keep the check
in their own tests. The `request-effect` reader answers the third-reader request
in that thread; the `uncertain-effect` reader is an outside implementation of a
corpus neither of us wrote. AlgoVoi also contributed one case to the
`request-effect-join` contract, S-10, a dispatched attempt whose outcome is not
completed, which exercises a branch the contract named but the corpus did not
build.

## Run them

Each directory is self contained. Both fetch their corpus by commit from the
forge and install their harness from PyPI, so a clean machine reproduces the
figure without a checkout of any repository here.

```sh
cd uncertain-effect
bash run.sh                  # reference rail, this reader, the axis report, the audit probes
bash check_reader_bites.sh   # disable each check in turn and require the corpus to go red

cd ../request-effect
bash run.sh                  # stage the contract, check its digests, then reader, bites, probes, canonicaliser
```

`WORK=<dir>` moves the working directory; `COMMIT=`, `HARNESS=` and
`SITE_COMMIT=` repin the inputs.

## Validated on VM2

Build, functional and security validation ran on VM2 in fresh Docker containers,
on `python:3.12-slim` and at the floor the harness supports, `python:3.11-slim`,
against a tree staged with `git archive`. Both legs passed 8 of 8 stages, with
`bandit` and `shellcheck` clean, and three negative controls including a
permissive verifier that the contract caught 34 times. The record, including the
two defects the box found and the workstation could not, is
[`VALIDATION-2026-11-20.md`](VALIDATION-2026-11-20.md).

## What a green figure establishes, and what it does not

It establishes that the stated checks are implementable by a second pair of
hands, that every check changes an answer when it is disabled, and that the
reader emits no reason outside the vocabulary the corpus publishes.

It does not establish that either specification is determinate from a cold start.
Both readers are **directed**: their author had read the reference implementations
before writing them. It says nothing about a producer's honesty, about an effect
owner's ledger being correct, about clocks, or about the receipt, request and
effect binding axes that a trail record carries no evidence for. Each reader's
record states its own limits, including the environment it was and was not run in.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.

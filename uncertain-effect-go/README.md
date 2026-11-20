# A Go reader for `uncertain-effect/v1`

A second implementation of the fifteen ordered checks the corpus
`corpora/vectors-uncertain-effect` states, run through that corpus's own
external-verifier contract:

```
<verifier> <case-dir>/record.json --json
```

prints one JSON object carrying `decision` and `reason`, and exits 0 for
`allow` and 1 for `deny`.

## What this adds, and what it does not

It is a **port of the Python reader in this repository**, not an independent
reading of the specification: the same author wrote both, and the Go code follows
the same reading of the same text. What it adds is a second language, a second
code path with its own JSON handling, and a static binary with no runtime to
install. What it does not add is a blind result, or evidence that the text is
determinate from a cold start.

Every record in this repository has said, since the first run, that there was no
second implementation in another language. This is that gap closed, and nothing
more than that.

## Result at the pinned inputs

| | |
| --- | --- |
| Corpus | `corpora/vectors-uncertain-effect` at `6ba08cbe9d7f2819e3c47c84adee2fa65390e123` |
| Harness | `agent-evidence-vectors==0.37.1` from PyPI |
| Toolchain | Go 1.22.2, fetched by `run.sh` when the machine has none |
| Binary | 2,666,317 bytes, `CGO_ENABLED=0 -trimpath`, static |
| Reference rail | 54 of 54 cases, 0 disagreements |
| **This reader** | **54 of 54 cases, 0 disagreements** |

Run on VM2 on 2026-11-20 in fresh containers, on CPython 3.12 and 3.11 with the
Go toolchain inside each: `gofmt` clean, `go vet` clean, 40 probes passing, the
mutation matrix reporting `coverage: 15 checks, 15 deny reasons, none uncovered`
with all fifteen checks biting. The record is
[`../VALIDATION-2026-11-20.md`](../VALIDATION-2026-11-20.md).

`gosec` is not part of that gate: VM2 has 1.8 GB of RAM and building `gosec`
pulls close to a gigabyte of modules, which the box OOM-kills. It was run on a
workstation instead, where it reported two findings on the one `os.ReadFile` call
(G304 and G703, path traversal by taint analysis). That is the tool's own
command-line argument, with no untrusted source and no privilege boundary, so the
finding is recorded and annotated in the source rather than fixed; the scan then
reports nothing.

## The gates

| Gate | What it establishes |
| --- | --- |
| `gofmt -l .` | the source is formatted, so a later diff is a change and not a reflow |
| `go vet ./...` | no suspicious constructs |
| `go test ./...` | 40 probes: every malformed shape the corpus never builds, each with a control, plus a check that all fifteen reasons have a predicate and are reachable from the ordered list |
| `check_checks_bite.sh` | each of the fifteen predicates is disabled in a copy of the source, rebuilt, and the cases expecting that reason must be reported as disagreements. Before the loop it asserts that every named check has a deny case, so the matrix cannot silently skip one |
| `run.sh` | corpus staged at its pinned commit, reference rail first, then this reader behind the same contract |
| an edited record | one effect becomes two against one attempt, and the reader must name `owner_effect_unaccounted` |

The binary is built with `CGO_ENABLED=0` and `-trimpath`, so it is static and
carries no build paths.

## Run it

```sh
bash run.sh              # toolchain, gofmt, vet, tests, build, then both rails
bash check_checks_bite.sh # rebuild with one check disabled, fifteen times
```

Both fetch the corpus by commit from the forge and the harness from PyPI, and both
install the pinned Go toolchain (`1.22.2`) when the machine has none. `WORK=<dir>`
moves the working directory; `COMMIT=`, `HARNESS=` and `GO_VERSION=` repin the
inputs.

## What it does not establish

- Not blind: see above.
- Not independent of the Python reader. Two code paths agreeing is worth
  something; one author writing both is worth less than two authors.
- Nothing about a producer's honesty, the owner's ledger being correct, or clocks.
- Nothing about the receipt, request and effect binding axes: a trail record
  carries no evidence for them.

## Licence

Apache-2.0, as the rest of this repository.

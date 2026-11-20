# VM2 validation, 2026-11-20

Build, functional validation and security validation of this repository on VM2,
in fresh Docker containers, against the staged tree
`b1fd6e1cb8ba8e3bce52c114d608d68947f4e34f` (source tarball sha256
`c984f6158bfaf95216b631762b9962e6540f12e2efd1100fcfcd642f6d875871`).

That tree is an index tree, not a commit: it is what `git write-tree` produced
from the staged change, and `git archive` shipped those bytes. The commit that
adds this record carries the same tree plus two documents, this one and the README
section that links it, and nothing else: `git diff --stat b1fd6e1c HEAD` prints
those two paths. No script reads either, which is what lets the gate result
transfer across the difference.

Nothing here was run from a workstation. The tree was staged from `git archive`
of the index, so what the containers read is what the repository holds, not a
checkout with a line ending conversion applied.

## Containers

| Container | Base | Mount | Role |
| --- | --- | --- | --- |
| `readers312-clean` | `python:3.12-slim` | `/root/readers-val` | full gate |
| `readers311-clean` | `python:3.11-slim` | `/root/readers-val-py311` | full gate, at the floor the harness supports |
| `readers-sec-clean` | `python:3.12-slim` | `/root/readers-sec` | static analysis and secret scan |

Each container has its own mount and its own venv. The harness is
`agent-evidence-vectors==0.37.1` and the canonicaliser `rfc8785==0.1.4`,
installed from PyPI into a fresh venv per container; both are asserted by version
after install, because a gate that runs against a different harness is testing
that harness.

`agent-evidence-vectors` declares `Requires-Python >=3.11`, which is why the floor
container is 3.11 and not 3.10.

## Functional result

| Stage | 3.12 | 3.11 |
| --- | --- | --- |
| venv, pip, harness 0.37.1, rfc8785 0.1.4 | PASS | PASS |
| harness version and canonicaliser import asserted | PASS | PASS |
| `uncertain-effect/run.sh`: reference rail, reader rail, axis report, audit probes | PASS | PASS |
| `uncertain-effect/check_reader_bites.sh`: mutation matrix | PASS | PASS |
| `request-effect/run.sh`: digests, reader, bite matrix, named probes, canonicaliser | PASS | PASS |
| `uncertain-effect-go/run.sh`: gofmt, vet, 40 probes, static build, both rails | PASS | PASS |
| `uncertain-effect-go/check_checks_bite.sh`: 15 mutations on the Go source | PASS | PASS |
| **Total** | **10 of 10 stages** | **10 of 10 stages** |

Both interpreters, in the same run:

- reference reader over `corpora/vectors-uncertain-effect` at `6ba08cb`: 54 of 54 cases, 0 disagreements;
- AlgoVoi reader through the external-verifier contract: 54 of 54 cases, 0 disagreements;
- derived settled outcomes compared with the corpus declaration: 0 disagreements;
- audit probes: 0 did not hold;
- mutation matrix: 15 checks disabled in turn, every one turned the corpus red, 0 missed, and the edited record was named `owner_effect_unaccounted`;
- the matrix first asserts its own coverage, and prints `coverage: 15 checks, 15 deny reasons, none uncovered`, so a check with no deny case fails the gate instead of being quietly skipped.

`request-effect` at `probityai.github.io` `808041f5`, with both manifest digests
checked before either file was read:

- classifications: `established` 4, `refused` 3, `invalid_input` 2, `not_established` 2;
- reader exit 0, every `must_not_claim` prohibition honoured;
- 45 bite checks passed;
- 21 named probes and controls passed;
- 56 preimages, 400 generated values, 0 divergences from `rfc8785` 0.1.4.

### The Go reader

The same corpus read by a second implementation in Go, built to a static binary
(2,666,317 bytes, `CGO_ENABLED=0 -trimpath`) with Go 1.22.2 fetched inside the
container:

- `gofmt` clean, `go vet` clean, 40 probes passing;
- reference rail 54 of 54, Go reader 54 of 54, 0 disagreements;
- mutation matrix `coverage: 15 checks, 15 deny reasons, none uncovered`, with all
  fifteen checks biting when disabled in the source and rebuilt.

It is a port of the Python reader by the same author. It closes the "no second
language" gap every earlier record named, and it does not close the "second
author" one.

## Security result

| Stage | Result |
| --- | --- |
| `bandit` 1.8.6 over the whole tree | 0 issues |
| `shellcheck` 0.11.0.1 at warning and above, every shipped script | 0 findings |
| secret and workstation-path scan, calibrated | 22 files, 0 hits |
| planted-secret control | fired on both the AWS key and the GitHub token pattern |
| `py_compile` on every shipped module | clean |
| `bash -n` on every shipped script | clean |
| `go vet ./...` | clean |
| `gosec` | **not run on VM2**: the box has 1.8 GB of RAM and building it pulls close to a gigabyte of modules, which the kernel kills. Run on a workstation instead: two findings (G304, G703) on the single `os.ReadFile` of the reader's own command-line argument, recorded and annotated as no privilege boundary; the rescan reports nothing |

The scan was calibrated before it was trusted: a bare 64 hex character rule
matches every sha256 digest in this tree and reports hundreds of false hits, so
the digest pattern is absent by design and the control is what proves the
remaining patterns still fire.

## The negative controls

A green gate proves nothing unless it can go red, so three separate controls ran:

1. **the mutation matrix**, which disables each of the 15 checks in its own copy
   of the reader and requires the corpus to report disagreements for the cases
   that expect that reason. All 15 bit.
2. **the edited record**, where one effect becomes two against one attempt and the
   reader must name `owner_effect_unaccounted` rather than pass on its bytes.
3. **a permissive verifier**, a stub that answers `allow` for every case and was
   driven through the same contract. It answered 54 of 54 with **34
   disagreements** and exit 1, which is the number of deny cases in the corpus.
   This control was run separately from the gate, on the 3.12 container.

## What the box found that the workstation could not

Two defects, both of which a Linux user would have met and neither of which the
Windows run could see:

- **The venv entry point was resolved before the venv existed.** Both scripts
  picked `Scripts/` or `bin/` by testing whether the directory was there, and on a
  clean tree neither is there yet, so a Linux run chose `Scripts`, created the
  venv, and then called a pip that had never been made
  (`/work/src/uncertain-effect/.run/venv/Scripts/pip: No such file or directory`,
  exit 127). It passed on the workstation only because a venv from an earlier run
  was already on the path being tested. The resolution now happens after the venv
  is created, keyed on whether the interpreter itself is present.
- **Five gate scripts leaned on `assert`**, which `python -O` removes. A gate
  whose checks vanish under an optimisation flag is worse than no gate, so those
  are now explicit `SystemExit` raises. `bandit` reported 6 `B101` and one `B311`
  before the fix; the `B311` (seeded `random` generating test values) is annotated
  as no-security-use rather than removed. `shellcheck` also flagged an unchecked
  `cd` in the mutation script, now `|| exit 1`.

## What the outside review found

An independent pass by OpenAI Codex (`gpt-5.6-terra`), given the tree and told to
re-derive the claims and hunt for fail-open paths, returned six findings. Each was
tested before it was acted on, and one was refuted:

| Finding | Disposition |
| --- | --- |
| HIGH: the mutation loop disables only the deny reasons in the manifest, so with 14 deny reasons for 15 checks one predicate is never disabled | **Partly right, and acted on.** The count was wrong: the manifest carries 15 distinct deny reasons and the gate prints 15 `bites disable` lines, but the reviewer was quoting this repository's own text, which said 14. That was a defect in the document and is corrected. The structural point stood too: the loop trusted the corpus to cover every check, so it now asserts coverage and fails when a check has no deny case. |
| HIGH: a negative `ownerEffects` passes both ledger checks, because `-1 > could` and `-1 > 1` are both false | **Real, fixed.** A record with `ownerEffects: -1` was allowed with exit 0; it now denies `duplicate_effect`. A probe holds it. |
| MEDIUM: the request-effect digest stage accepts a manifest whose `sha256` mapping is empty, verifying nothing and reporting ok | **Real, fixed.** The stage now fails when either required digest is undeclared. |
| MEDIUM: the recorded sha256 for `check_audit_probes.py` did not match the file | **Real, fixed.** The pin recorded the file before the review of the gate scripts, and the document was not updated with it. |
| MEDIUM: the validated tree was cited as a commit and does not resolve in the published repository | **Real, fixed.** The record now says it is an index tree and gives the published commit's tree beside it. |
| LOW: a reader that exits 0 with unparseable JSON lets the request-effect stage continue to `PASS` | **Real, fixed.** The parse is now a stage: an unreadable report fails rather than printing a summary. |

A third pass on the coverage assertion confirmed it closes the finding and raises
its own edge case: with an empty `FIRST_FAILING` the comparison is vacuous and
prints "none uncovered". The stage now fails when the reader names no checks, and a
control that blanks the tuple in a copy of the reader fails with that message and
exit 1. That two-line guard has not itself been re-reviewed.

The probe script gained one probe (38 to 39), the reader gained the negative-count
refusal, and the mutation script gained its coverage assertion and guard, so the
gate was re-run three times on revised trees; the figures above are the last run's,
on `896915fa`.

### What the outside review found in the Go reader

The same reviewer audited the Go implementation against the fifteen checks as
stated. Ten findings, each tested before it was acted on:

| Finding | Disposition |
| --- | --- |
| BLOCKER: attempts with no lease fields are allowed when no `leases` array exists, so check 6 is not enforced | **Refuted.** Twenty of the corpus's allow cases carry no `leases` array and no worker fields, and the corpus's own reference reader allows them. Requiring a lease for every attempt would make the corpus unsatisfiable, so the check reads a claim of a lease as the thing that must be verifiable. |
| HIGH: an entry in `attempts` that is not an object is dropped, so the trail is judged as if the slot were empty | **Real, fixed.** Such a record now fails the identity check, and a probe pins it. |
| HIGH: check 5 refuses a trigger that is not an object of a known kind | **Kept, by design.** A trigger the reader cannot read cannot be shown to carry the operation's key. Both readers take this reading and both pin it with probes. |
| HIGH: an absent retention window is read as unlimited, and elapsed time was truncated to whole seconds | **Half real.** `keyRetentionSeconds: null` means no limit in the format's own declaration, so that part stands. The truncation was real and is now a duration comparison rather than an integer cast. |
| HIGH: a final reconciliation stamped at the exact instant of the last unknown attempt counts as "after" it | **Kept, recorded as a ruling.** Both readers treat equality as after, because a second-resolution stamp at the same instant is the case where an absent answer most plausibly covers the dispatch. The corpus has no case at the boundary, so it cannot discriminate. |
| MEDIUM: the same inclusivity in `absentAnswerBetween` | Same ruling, same reason. |
| MEDIUM: check 13 refuses any post-revocation authority result that is not `refused`, where the text forbids only `accepted` | **Kept, fail-closed.** A result the reader cannot read is not evidence that authority was withheld. Same in both readers. |
| MEDIUM: a negative ledger count is refused as `duplicate_effect`, which the literal predicates do not require | **Kept, decided on merit.** This is the same reviewer's earlier finding asking for exactly this refusal on the Python reader. Two passes contradicted each other; a ledger count of -1 is not a count of effects, and the stricter rule is the one a gate should hold. |
| MEDIUM: a malformed `stop` collapses to "no stop" | **Recorded as a shared limitation.** The format has no reason for a malformed stop, and inventing one would put a word in the corpus's mouth. Both readers behave the same way. |
| MEDIUM: the tests do not pin every check | **Real, fixed.** Eight probes added, taking the suite from 32 to 40, so each of the fifteen is reached directly. |

## What this does not establish

- One harness version. The corpus and the contract are read through
  `agent-evidence-vectors==0.37.1`, the version the records pin; a later harness
  could change the rail's own behaviour.
- No second language implementation. Both readers are Python, so nothing here
  speaks to whether the specifications are implementable in another language.
- Both readers remain **directed**, as their records say. A green run on two
  interpreters is not a blind result.
- The `request-effect` reader is unchanged since the ten case run; only the
  contract moved. S-10 is this project's own case, so its agreement is
  self-consistency rather than outside evidence.

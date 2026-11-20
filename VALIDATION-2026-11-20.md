# VM2 validation, 2026-11-20

Build, functional validation and security validation of this repository on VM2,
in fresh Docker containers, against the staged tree
`896915fa8c07fedf50f0c03de216696fe1928e32` (source tarball sha256
`eede2b50846fc26abc914eed56d90e41c76c56b9ab5090b3985224e499b5b6f1`).

That tree is an index tree, not a commit: it is what `git write-tree` produced
from the staged change, and `git archive` shipped those bytes. The commit that
adds this record carries the same tree plus two documents, this one and the README
section that links it, and nothing else: `git diff --stat 896915fa HEAD` prints
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
| **Total** | **8 of 8 stages** | **8 of 8 stages** |

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

## Security result

| Stage | Result |
| --- | --- |
| `bandit` 1.8.6 over the whole tree | 0 issues |
| `shellcheck` 0.11.0.1 at warning and above, every shipped script | 0 findings |
| secret and workstation-path scan, calibrated | 22 files, 0 hits |
| planted-secret control | fired on both the AWS key and the GitHub token pattern |
| `py_compile` on every shipped module | clean |
| `bash -n` on every shipped script | clean |

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

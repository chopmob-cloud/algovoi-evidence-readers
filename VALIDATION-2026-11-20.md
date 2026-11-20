# VM2 validation, 2026-11-20

Build, functional validation and security validation of this repository on VM2,
in fresh Docker containers, against the staged tree
`da252f3f503a78926abd339b8a286f97b62a01b2` (source tarball sha256
`bebc9ec9bcf61964f939361f2c6103d4f50f443aa32f722ea9c51d1c911886e8`).

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
- mutation matrix: 15 checks disabled in turn, every one turned the corpus red, 0 missed, and the edited record was named `owner_effect_unaccounted`.

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
| secret and workstation-path scan, calibrated | 21 files, 0 hits |
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

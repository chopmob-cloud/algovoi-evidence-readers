# An outside canonicaliser for `jcs-byte-vectors`

A second implementation of RFC 8785 canonical JSON, written against the
specification text, run through Probity AI's
[`corpora/jcs-byte-vectors`](https://github.com/probityai/agent-evidence-vectors/tree/main/corpora/jcs-byte-vectors)
oracle at the revision pinned in [`RUN-2026-11-24.md`](RUN-2026-11-24.md).

The corpus answers one question a canonicaliser cannot answer about itself:
whether an implementation that did not write the vectors prints the same bytes.
A serialiser that sorts keys, escapes what Python escapes and formats numbers
the way Python formats them agrees with itself on every case it generated, and
disagrees with the specification on the three boundaries the corpus names.

## The contract

The corpus takes a command. It writes one JSON value to the command's stdin and
reads canonical bytes from stdout, with no trailing newline, and treats a
non-zero exit as a refusal:

```sh
node check.mjs --verifier python algovoi_jcs_canonical.py
node check.mjs --profile bounded --verifier python algovoi_jcs_canonical.py --profile bounded
```

The bounded profile is requested by passing `--profile bounded` to the verifier
itself. The driver has no other way to signal it, and the profile is a reading of
the value, not of the invocation: it admits integers only, within the IEEE-754
safe range, at most 128 nested containers, and no Unicode noncharacter in a
member name or a string value.

## Run it

```sh
bash run.sh                  # fetch the pinned corpus, check its digests, run both profiles
bash check_canonical_bites.sh  # disable each rule in turn and require a named case to change
```

`WORK=<dir>` moves the working directory; `PYTHON=`, `NODE=`, `COMMIT=` and the
two digest variables repin the inputs. `run.sh` exits non-zero unless the `jcs`
profile passes and the bounded profile either passes or disagrees only inside
the drift family, which is the conflict recorded below. If that is fixed
upstream the script says so instead of expecting it.

No dependencies beyond `node` and Python 3.11 or later: the reader is stdlib
only, and it never reads `cases.json`.

## Result

`jcs`: **108 of 108 checks pass** at revision `2276455` (`cases.json`
`100dda04…`, `check.mjs` `30a7b1d9…`). That is 5 byte vectors, 18 drift
presentations, 23 refusals, 10 depth cases, 44 noncharacter cases and 8 preimage
cases.

`bounded`: the reader refuses `1e-7` as the profile requires, and the driver
then asks it to print `1e-7` from a drift case. Both cannot hold. See
[`RUN-2026-11-24.md`](RUN-2026-11-24.md) for the isolation and for the second
finding, that fifteen `invalidInputCases` are carried by the corpus and read by
no driver.

## The gates

`check_canonical_bites.sh` disables one rule at a time in a copy of the reader
and requires a named case to change. Five run the whole corpus under `jcs`:
UTF-16 member order, the ECMAScript number form, literal non-ASCII printing,
duplicate-member refusal and lone-surrogate refusal. Three are targeted at a
single case under `bounded` (integer admission, the 128-container cap, the
noncharacter refusal), because the drift conflict stops a bounded corpus run
before those families are reached.

## What a green figure establishes, and what it does not

It establishes that the three boundaries named in the corpus's own table are
implementable from the specification text by a second pair of hands, that every
rule changes an answer when disabled, and that the reader prints no byte the
oracle did not pin.

It does not establish determinacy: this run is labelled **directed**, because
the second version of the reader was written after reading the driver. It says
nothing about the corpus revisions after the one pinned here, and nothing about
any other corpus.
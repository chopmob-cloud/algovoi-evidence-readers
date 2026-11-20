#!/usr/bin/env python3
"""Report the reader's axis view over the corpus and check its derivations.

Two things this settles, neither of which the corpus's own comparison does:

  * the reader's settled outcome is derived independently of the corpus's
    reference reader, so agreeing with the manifest's `retained.outcome` on
    every case is a check on the derivation, not a restatement of it;
  * the axis distribution shows what an all-axes reader sees beside a
    first-failing one, which is where the two models differ.

Exits non-zero if any derived value disagrees with the corpus declaration.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path


def load_reader(path: Path):
    spec = importlib.util.spec_from_file_location("algovoi_uncertain_effect", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before executing: a module holding a dataclass is looked up in
    # sys.modules by dataclasses itself, and an unregistered one raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="path to vectors-uncertain-effect")
    parser.add_argument("--reader", type=Path, default=Path(__file__).with_name("algovoi_uncertain_effect.py"))
    args = parser.parse_args()

    reader = load_reader(args.reader)
    manifest = json.loads((args.corpus / "MANIFEST.json").read_text(encoding="utf-8"))
    reasons: collections.Counter = collections.Counter()
    axes: collections.Counter = collections.Counter()
    settled_counts: collections.Counter = collections.Counter()
    mismatches: list[str] = []

    for entry in manifest["vectors"]:
        trail = reader.read(args.corpus / entry["path"] / "record.json")
        answer = reader.judge(trail)
        view = reader.native(trail)
        reasons[answer["reason"]] += 1
        settled_counts[trail.settled()] += 1
        for axis in ("idempotency", "completion", "outcome_authority"):
            axes[f"{axis}={view[axis]['result']}"] += 1
        if answer != entry["expected"]:
            mismatches.append(f"{entry['id']}: decision {answer} != expected {entry['expected']}")
        declared = entry["retained"]["outcome"]
        if trail.settled() != declared:
            mismatches.append(f"{entry['id']}: settled {trail.settled()} != declared {declared}")

    print(f"cases: {len(manifest['vectors'])}")
    print(f"settled outcomes (derived): {dict(sorted(settled_counts.items()))}")
    print("first-failing reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    print("axes: " + ", ".join(f"{k}={v}" for k, v in sorted(axes.items())))
    for line in mismatches:
        print("DISAGREES " + line)
    print(f"{len(mismatches)} disagreements")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())

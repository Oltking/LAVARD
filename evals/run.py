"""Nightly-style eval runner for the Router's step classifier (§4.4).

    python -m evals.run            # prints accuracy, exits non-zero if below the floor

Keep the accuracy floor here in lockstep with the classifier. Add cases as new step shapes appear.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from core.router import classify_step

FLOOR = 0.90


def main() -> int:
    data = json.loads((Path(__file__).parent / "classification.json").read_text())
    cases = data["cases"]
    wrong = []
    for c in cases:
        got = classify_step(c["text"])
        if got != c["tier"]:
            wrong.append((c["text"], c["tier"], got))
    acc = 1 - len(wrong) / len(cases)
    print(f"classifier accuracy: {acc:.0%} ({len(cases) - len(wrong)}/{len(cases)})")
    for text, want, got in wrong:
        print(f"  MISS: want {want:<8} got {got:<8} — {text}")
    if acc < FLOOR:
        print(f"FAIL: below floor {FLOOR:.0%}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

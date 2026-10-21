"""`python -m ratchet.receipt verify|seal`: check a savings receipt offline, or seal a ledger's receipts (FR-15.5, ADR
0029). The logic lives in `ratchet/receipts.py`."""

from __future__ import annotations

import sys

from .receipts import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))

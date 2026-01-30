"""
Convenient root test launcher.

Usage:
    python run_tests.py            # runs all tests quietly
    python run_tests.py -m api     # only API/integration-marked tests

Why: simplifies running from IDE/Windows without remembering pytest args.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        import pytest  # type: ignore
    except ImportError:  # pragma: no cover
        sys.stderr.write("pytest is not installed. Try: pip install -r requirements-dev.txt\n")
        return 1

    root = Path(__file__).resolve().parent
    args = sys.argv[1:] or ["-vv"]
    # ensure repo root on sys.path so imports work
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    print("Running pytest with args:", " ".join(args))
    return pytest.main(args, plugins=[])


if __name__ == "__main__":
    raise SystemExit(main())

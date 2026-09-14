"""Runner-free entry point for the schema verification (stdlib only).

Thin wrapper around backend/app/tests/test_schema.py -- it runs that suite
and nothing else. The assertions (EXPECTED_COLUMNS, round-trips, uniqueness,
RESTRICT, FK checks) live in ONE place, test_schema.py, so a future schema
change is made once and both entry points stay in agreement by construction.

Usage (from the repo root):
    python3 backend/verify_schema.py

Exit 0 + "ALL CHECKS PASS" on success, nonzero + "CHECKS FAILED" otherwise.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "app" / "tests"))

import test_schema  # noqa: E402  (path set up above so script-style runs work)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_schema.SchemaTest)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print("ALL CHECKS PASS" if result.wasSuccessful() else "CHECKS FAILED")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

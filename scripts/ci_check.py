"""Run with the querydoctor package installed: python scripts/ci_check.py --help."""
from querydoctor.ci.runner import main

if __name__ == "__main__":
    raise SystemExit(main())

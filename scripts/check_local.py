"""Report exactly what is missing before local mode can take a call.

    uv run python scripts/check_local.py
    uv run python scripts/check_local.py --json

Exits 0 when every check passes or only warns, and 1 when something must be fixed first. Nothing is downloaded:
a missing model is reported with the command that fetches it, because a multi-gigabyte download is the
operator's decision to make.
"""

import argparse
import json
import sys

from preauth.infrastructure.settings import Settings
from preauth.local.config import LocalSettings
from preauth.local.diagnostics import FAIL, OK, WARN, run_checks, summarise

MARK = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    report = summarise(run_checks(LocalSettings.from_env(), settings.database_url))

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ready"] else 1

    print("Local mode readiness\n")
    for check in report["checks"]:
        print(f"  {MARK[check['status']]:4}  {check['name']:<16}  {check['detail']}")
        if check["fix"]:
            print(f"        {'':<16}  fix: {check['fix']}")
    print()
    if report["ready"]:
        print(f"Ready to take local calls ({report['warnings']} warning(s)).")
        print("Start it with:  ./scripts/run_local.sh")
        return 0
    print(f"{report['failures']} check(s) must be fixed before local mode will work.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

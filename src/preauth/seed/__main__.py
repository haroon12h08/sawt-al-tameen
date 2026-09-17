"""Load the UAE benefit catalogue and (optionally) demonstration scenarios.

Usage (after ``alembic upgrade head``):
    python -m preauth.seed                # catalogue only
    python -m preauth.seed --scenarios    # reference data + the five demonstration cases
"""

import argparse
import sys

from preauth.application.services import build_services
from preauth.infrastructure.clock import SystemClock
from preauth.infrastructure.db.models import PolicyTier
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.observability import configure_logging
from preauth.infrastructure.settings import Settings
from preauth.seed.catalogue import is_loaded, load_catalogue
from preauth.seed.scenarios import run_scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", action="store_true", help="also create the demonstration cases")
    parser.add_argument(
        "--if-empty", action="store_true", help="succeed without changes when reference data already exists"
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    configure_logging("WARNING")
    session_factory = build_session_factory(build_engine(settings.database_url))
    clock = SystemClock()

    with session_factory() as session:
        if is_loaded(session):
            if args.if_empty:
                print("Catalogue already loaded; nothing to do.")
                return 0
            print("Catalogue already loaded; refusing to load it twice.", file=sys.stderr)
            return 1
        counts = load_catalogue(session)
        session.commit()
    print(
        "Loaded the UAE catalogue from knowledge_base/: "
        + ", ".join(f"{v} {k}" for k, v in counts.items())
        + "."
    )

    if args.scenarios:
        for result in run_scenarios(build_services(session_factory, clock=clock), clock.today()):
            print(f"  {result.case_reference}  {result.name}: {result.outcome} -> {result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

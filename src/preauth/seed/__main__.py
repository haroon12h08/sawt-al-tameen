"""Load synthetic reference data and (optionally) demonstration scenarios.

Usage (after ``alembic upgrade head``):
    python -m preauth.seed                # reference data only
    python -m preauth.seed --scenarios    # reference data + the five demonstration cases
"""

import argparse
import sys

from sqlalchemy import func, select

from preauth.application.services import build_services
from preauth.infrastructure.clock import SystemClock
from preauth.infrastructure.db.models import Provider
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.observability import configure_logging
from preauth.infrastructure.settings import Settings
from preauth.seed.reference_data import load_reference_data
from preauth.seed.scenarios import run_scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", action="store_true", help="also create the demonstration cases")
    args = parser.parse_args()

    settings = Settings.from_env()
    configure_logging("WARNING")
    session_factory = build_session_factory(build_engine(settings.database_url))
    clock = SystemClock()

    with session_factory() as session:
        if session.scalar(select(func.count(Provider.id))):
            print("Reference data already present; refusing to load it twice.", file=sys.stderr)
            return 1
        load_reference_data(session, clock.today())
        session.commit()
    print("Loaded synthetic reference data.")

    if args.scenarios:
        for result in run_scenarios(build_services(session_factory, clock=clock), clock.today()):
            print(f"  {result.case_reference}  {result.case_id}  {result.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

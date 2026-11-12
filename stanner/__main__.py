"""CLI.

  python -m stanner setup                              create dataset, tables, views
  python -m stanner run youtube news reddit ticketmaster   (hourly)
  python -m stanner run musicbrainz setlistfm              (daily)
  python -m stanner run <sources> --dry-run            fetch and print, no BigQuery
  python -m stanner seed fetch | load                  one-time historical seed
"""

import argparse
import sys


def main():
    p = argparse.ArgumentParser(prog="stanner")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    r = sub.add_parser("run")
    r.add_argument("sources", nargs="+")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--full", action="store_true", help="ignore watermark; re-fetch all the source exposes")
    pg = sub.add_parser("purge", help="delete all events from the given sources")
    pg.add_argument("sources", nargs="+")
    pg.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s = sub.add_parser("seed")
    s.add_argument("step", choices=["fetch", "load"])
    s.add_argument("--sources", nargs="+", default=["musicbrainz", "youtube", "setlistfm"])
    a = p.parse_args()

    if a.cmd == "setup":
        from .bq import setup

        setup()
    elif a.cmd == "run":
        from .pipeline import SOURCES, run_source

        unknown = set(a.sources) - set(SOURCES)
        if unknown:
            p.error(f"unknown sources {unknown}; choose from {sorted(SOURCES)}")
        # Run every source even if one fails; exit non-zero so the failure is visible in Actions.
        results = [run_source(name, dry_run=a.dry_run, full=a.full) for name in a.sources]
        sys.exit(0 if all(results) else 1)
    elif a.cmd == "purge":
        from .bq import purge

        if not a.yes and input(f"Delete ALL events from {a.sources}? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
        print(f"removed {purge(a.sources)} rows")
    elif a.cmd == "seed":
        from . import seed

        seed.fetch(tuple(a.sources)) if a.step == "fetch" else seed.load()


if __name__ == "__main__":
    main()

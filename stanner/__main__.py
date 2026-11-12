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
        results = [run_source(name, dry_run=a.dry_run) for name in a.sources]
        sys.exit(0 if all(results) else 1)
    elif a.cmd == "seed":
        from . import seed

        seed.fetch(tuple(a.sources)) if a.step == "fetch" else seed.load()


if __name__ == "__main__":
    main()

"""Command-line interface."""

import argparse
import sys

from . import __version__
from . import config as config_mod
from .db import open_db

EPILOG = """Typical workflow:
  archivero scan   /mnt/nas/alice --db nas.db
  archivero dupes  --db nas.db
  archivero archives list --db nas.db         # what is inside zips and disk images
  archivero report --db nas.db --out report
  archivero plan   --db nas.db --dest /Volumes/New --out plan.csv
  archivero apply  --plan plan.csv            # dry run
  archivero apply  --plan plan.csv --execute  # real copy (never deletes or moves)
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="archivero", epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description="Inventory, classify and plan the cleanup of large file trees.")
    ap.add_argument("--version", action="version", version="archivero " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--db", default="inventory.db", help="SQLite inventory database")
        p.add_argument("--config", help="TOML file with options (see config.py)")

    p = sub.add_parser("scan", help="phase 1: walk and classify (resumable)")
    p.add_argument("root")
    common(p)
    p.add_argument("--exif", action="store_true", help="read photo EXIF dates (requires Pillow)")
    p.add_argument("--probe-video", action="store_true", help="video duration and date (requires ffprobe)")
    p.add_argument("--tags", action="store_true", help="audio tags: artist, album (requires mutagen)")
    p.add_argument("--no-measure", action="store_true",
                   help="don't measure node_modules, .git, trash folders… (faster)")
    p.add_argument("--archives", action="store_true",
                   help="also list the contents of archives and disk images (reads their index only)")

    p = sub.add_parser("archives", help="list or inspect archives and disk images without extracting them")
    p.add_argument("action", choices=["list", "inspect"],
                   help="list: read indexes (cheap) · inspect: hash every member in memory (thorough)")
    p.add_argument("paths", nargs="*", help="archives to process (default for 'list': all)")
    common(p)
    p.add_argument("--suggested", action="store_true", help="inspect: the archives the report suggests")
    p.add_argument("--all", action="store_true", help="inspect: every archive")
    p.add_argument("--force", action="store_true", help="list: re-read archives already listed")

    p = sub.add_parser("dupes", help="phase 2: confirm duplicates by hash")
    common(p)
    p.add_argument("--workers", type=int, help="parallel reads (default 4)")

    p = sub.add_parser("report", help="phase 3: HTML report + CSV + cleanup proposals")
    common(p)
    p.add_argument("--out", default="report")
    p.add_argument("--depth", type=int, default=3, help="folder depth shown in the HTML report")

    p = sub.add_parser("plan", help="phase 4: propose a reorganization by type and date (CSV)")
    common(p)
    p.add_argument("--dest", required=True, help="root folder of the new structure")
    p.add_argument("--out", default="plan.csv")

    p = sub.add_parser("apply", help="phase 5: carry out the plan by copying")
    p.add_argument("--plan", default="plan.csv")
    p.add_argument("--execute", action="store_true", help="actually copy (dry run otherwise)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "apply":
            from .plan import apply_plan
            apply_plan(args.plan, args.execute)
            return
        cfg = config_mod.load(args.config)
        con = open_db(args.db)
        if args.cmd == "scan":
            from .scanner import scan
            scan(con, args.root, cfg, exif=args.exif, probe_video=args.probe_video, tags=args.tags,
                 measure_pruned=not args.no_measure)
            if args.archives:
                from .archives import list_containers
                list_containers(con, cfg)
        elif args.cmd == "archives":
            from .archives import inspect_containers, list_containers
            if args.action == "list":
                list_containers(con, cfg, args.paths or None, args.force)
            else:
                inspect_containers(con, cfg, args.paths or None, args.suggested, args.all)
        elif args.cmd == "dupes":
            from .dupes import find_duplicates
            if args.workers:
                cfg["dupes_workers"] = args.workers
            find_duplicates(con, cfg)
        elif args.cmd == "report":
            from .analysis import Analysis
            from .report import report
            report(Analysis(con, cfg), args.out, args.depth)
        elif args.cmd == "plan":
            from .analysis import Analysis
            from .plan import make_plan
            make_plan(Analysis(con, cfg), args.dest, args.out)
        con.close()
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted. Run the same command again to resume where it stopped.\n")
        sys.exit(130)


if __name__ == "__main__":
    main()

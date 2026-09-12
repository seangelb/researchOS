"""Preview an explicit refresh; add --live to collect, validate and back it up."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from variant_gaming.refresh import run_refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, help="STATE:online_sports_betting or STATE:online_casino")
    parser.add_argument("--run-dir", type=Path, required=True, help="New directory, relative to gaming/ or absolute")
    parser.add_argument("--backup", type=Path, required=True, help="New .zip path outside the repository")
    parser.add_argument("--base-db", type=Path, help="Existing database to preserve as a read-only starting point")
    parser.add_argument("--mode", choices=["recent", "history"], default="recent")
    parser.add_argument("--report-limit", type=int, default=2)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    try:
        sources = [tuple(value.split(":")) for value in args.source]
        if any(len(pair) != 2 for pair in sources):
            raise ValueError("Sources must have the form STATE:product")
        result = run_refresh(root=ROOT, run_dir=ROOT / args.run_dir, backup_path=args.backup,
                             sources=sources, base_db=ROOT / args.base_db if args.base_db else None,
                             mode=args.mode, report_limit=args.report_limit, live=args.live)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Refresh stopped: {exc}\n")
    print(json.dumps(result, indent=2))
    return 2 if result["status"] == "complete_with_exceptions" else 0


if __name__ == "__main__":
    raise SystemExit(main())

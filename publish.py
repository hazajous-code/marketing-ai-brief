"""Manual newsletter publish script.

Usage:
    python publish.py              # Generate today's issue + push
    python publish.py --all        # Regenerate ALL issues from archive + push
    python publish.py --no-push    # Generate only, skip git push
    python publish.py --date 2026-04-14  # Generate a specific date
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Marketing AI Brief newsletter")
    parser.add_argument("--all", action="store_true", help="Regenerate all archived issues")
    parser.add_argument("--date", type=str, default=None, help="Specific date (YYYY-MM-DD)")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit/push")
    args = parser.parse_args()

    from newsletter_builder import git_push, publish_all, publish_daily

    if args.all:
        print("Generating ALL issues from archive...")
        publish_all()
    elif args.date:
        print(f"Generating issue for {args.date}...")
        publish_daily(date_str=args.date)
    else:
        print("Generating today's issue...")
        publish_daily()

    if not args.no_push:
        print("Pushing to GitHub...")
        ok = git_push()
        if ok:
            print("Done! Check your GitHub Pages URL.")
        else:
            print("Git push failed. Check logs above.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Done (--no-push, skipped git).")


if __name__ == "__main__":
    main()

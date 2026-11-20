"""python3 -m recovery_coach run|backfill|weekly --in DIR --out DIR [--date YYYY-MM-DD]"""
from .cli import main

raise SystemExit(main())

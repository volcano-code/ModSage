#!/usr/bin/env python3
"""Read-only, bounded live source probe. Writes no raw author descriptions.

Example: PYTHONPATH=apps/api python scripts/steam_probe.py --limit 50
Only counts, IDs and observations are written to the ignored data directory.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from modsage.sources.steam import SteamClient, SteamSourceError  # noqa: E402


async def probe(limit: int, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    async with SteamClient(api_key=os.getenv("MODSAGE_STEAM_API_KEY")) as source:
        ids = (
            await source.query_files(limit=limit)
            if source.api_key
            else await source.discover_public(limit=limit)
        )
        discovery_mode = source.discovery_mode
        discovery_warnings = list(source.last_warnings)
        mods = await source.get_details(ids)
        report = {
            "observed_at": datetime.now(UTC).isoformat(),
            "appid": 1142710,
            "discovery_mode": discovery_mode,
            "official_query_files_authenticated": bool(source.api_key),
            "requested_ids": len(ids),
            "valid_mods": len(mods),
            "field_counts": {
                "title": sum(bool(m.title) for m in mods),
                "description": sum(bool(m.description) for m in mods),
                "published_at": sum(m.published_at is not None for m in mods),
                "updated_at": sum(m.updated_at is not None for m in mods),
                "subscribers": sum(m.subscribers is not None for m in mods),
                "favorites": sum(m.favorites is not None for m in mods),
                "preview_url": sum(m.preview_url is not None for m in mods),
                "dependencies_known": sum(m.dependencies_known for m in mods),
            },
            "warnings": discovery_warnings + source.last_warnings,
            "limits": [
                "Single bounded source probe; not a three-day continuity check",
                "Public HTML fallback is not full Workshop discovery coverage",
                "No LLM translation, game compatibility or in-game execution was validated",
                "An absent children field means unknown dependencies, not no dependencies",
            ],
        }
        (output / "probe_ids.json").write_text(json.dumps([m.id for m in mods], indent=2) + "\n")
        (output / "steam_probe_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    try:
        report = asyncio.run(probe(args.limit, args.output))
    except (SteamSourceError, ValueError) as exc:
        print(
            json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

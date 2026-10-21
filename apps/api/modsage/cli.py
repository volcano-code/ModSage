import argparse
import asyncio
import json
import logging
import re
import sys

from modsage.config import get_settings
from modsage.database import init_database, make_database
from modsage.digests import build_digest
from modsage.enrichment import enrich_mods
from modsage.ingestion import sync_ids
from modsage.jobs import enqueue_refresh, list_jobs, run_worker, sync_status


def parse_ids(values):
    ids = [x.strip() for value in values for x in value.split(",") if x.strip()]
    if not ids or len(ids) > 1000 or any(not re.fullmatch(r"[1-9][0-9]{0,19}", x) for x in ids):
        raise ValueError("Provide 1–1000 valid Workshop IDs")
    if any(int(x) > 2**64 - 1 for x in ids):
        raise ValueError("Workshop IDs must fit uint64")
    return list(dict.fromkeys(ids))


async def execute(args, settings, session_factory):
    if args.command == "init-db":
        from modsage import __version__

        return {"status": "ready", "schema_version": __version__}
    if args.command == "enqueue-refresh":
        return enqueue_refresh(
            session_factory, settings, idempotency_key=args.key, source=args.source
        )
    if args.command == "worker":
        return await run_worker(session_factory, settings, once=args.once)
    if args.command == "jobs":
        return {
            "items": list_jobs(session_factory, args.limit),
            "sync": sync_status(session_factory, settings),
        }
    if args.command == "digest":
        with session_factory.begin() as session:
            return build_digest(session, refresh=args.refresh)
    if args.command == "enrich":
        result = await enrich_mods(session_factory, settings, args.limit)
        if result["generated"]:
            with session_factory.begin() as session:
                build_digest(session, refresh=True)
        return result
    from modsage.sources.steam import SteamClient

    steam = SteamClient(api_key=settings.steam_api_key or None)
    try:
        if args.command == "sync":
            ids = parse_ids(args.ids)
            mode = "ids"
        elif args.command == "collection":
            ids = await steam.get_collection_ids(parse_ids([args.id])[0])
            mode = "collection"
        else:
            if args.public:
                ids = await steam.discover_public(limit=args.limit, sort=args.sort)
                mode = "public_limited"
            else:
                ids = await steam.query_files(limit=args.limit, sort=args.sort)
                mode = "api"
        if not ids:
            return {"imported": 0, "mode": mode, "warnings": ["来源未返回可导入条目。"]}
        result = await sync_ids(session_factory, steam, ids, mode=mode)
        # Explicitly refresh the current daily view after a successful source synchronization.
        with session_factory.begin() as session:
            build_digest(session, refresh=True)
        return result
    finally:
        await steam.aclose()


def main():
    parser = argparse.ArgumentParser(description="ModSage source and knowledge operations")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser(
        "init-db", help="Initialize the database, including additive refresh-job tables"
    )
    enqueue = subs.add_parser("enqueue-refresh", help="Queue one persistent daily-style refresh")
    enqueue.add_argument(
        "--key", help="Reuse an idempotency key to return the same job; default: a new UUID"
    )
    enqueue.add_argument(
        "--source", choices=["api", "public"], help="Override MODSAGE_REFRESH_SOURCE for this task"
    )
    worker = subs.add_parser(
        "worker", help="Run the lease-based refresh worker and optional daily scheduler"
    )
    worker.add_argument(
        "--once",
        action="store_true",
        help="Enqueue today's due task if enabled, then process at most one task",
    )
    jobs = subs.add_parser("jobs", help="Show bounded job history and scheduling/worker status")
    jobs.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")
    sync = subs.add_parser("sync", help="Fetch and import explicit Workshop IDs")
    sync.add_argument("--ids", nargs="+", required=True)
    collection = subs.add_parser("collection", help="Import a public collection by ID")
    collection.add_argument("--id", required=True)
    discover = subs.add_parser("discover", help="Discover and import Workshop entries")
    discover.add_argument("--limit", type=int, default=50, choices=range(1, 501), metavar="1..500")
    discover.add_argument("--sort", choices=["updated", "newest", "popular"], default="updated")
    discover.add_argument(
        "--public",
        action="store_true",
        help="Opt into limited public-page discovery; not complete API coverage",
    )
    digest = subs.add_parser("digest", help="Build the persistent Asia/Shanghai daily issue")
    digest.add_argument("--refresh", action="store_true")
    enrich = subs.add_parser(
        "enrich", help="Generate source-supported Chinese introductions using the configured model"
    )
    enrich.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    if args.command == "discover" and args.public and args.limit > 100:
        parser.error("Public discovery is bounded to at most 100 entries per operation.")
    settings = get_settings()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("modsage.jobs").setLevel(logging.INFO)
    engine, session_factory = make_database(settings.database_url)
    init_database(engine)
    try:
        result = asyncio.run(execute(args, settings, session_factory))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("errors") or result.get("status") in {
            "partial",
            "failed",
            "retry_wait",
            "lease_lost",
        }:
            return 1
        return 0
    except Exception as exc:
        from modsage.llm import ModelUnavailable
        from modsage.sources.steam import SteamConfigurationError

        if isinstance(exc, SteamConfigurationError):
            hint = "官方发现需要 MODSAGE_STEAM_API_KEY；也可显式 --public 使用有限公开目录。"
        elif isinstance(exc, ModelUnavailable):
            hint = "请检查 MODSAGE_LLM_API_KEY、MODSAGE_LLM_MODEL 及模型服务可用性。"
        else:
            hint = "操作失败；既有目录不会被空响应替换。请检查输入与来源可用性。"
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "message": hint},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

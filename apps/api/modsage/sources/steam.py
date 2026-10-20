"""Bounded Steam Workshop access for Total War: WARHAMMER III.

The public HTML discovery adapter is a limited fallback, not a full Workshop
index. Item descriptions are plain text. No page-template warning is interpreted
as compatibility evidence. A missing dependency list is never proof of no
dependencies.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

APP_ID = 1142710
API_BASE = "https://api.steampowered.com"
COMMUNITY_BASE = "https://steamcommunity.com"
_SORTS = {"updated": 21, "newest": 1, "popular": 3}
_PUBLIC_SORTS = {"updated": "lastupdated", "newest": "mostrecent", "popular": "trend"}
_USER_AGENT = "ModSage/0.1 (+https://github.com/volcano-code/ModSage)"


class SteamSourceError(RuntimeError):
    """Upstream transport, response-shape or availability failure."""


class SteamConfigurationError(SteamSourceError):
    """A requested capability needs a key or supported configuration."""


@dataclass(slots=True)
class SteamItem:
    id: str
    title: str
    description: str
    author: str | None
    source_url: str
    preview_url: str | None
    tags: list[str]
    published_at: datetime | None
    updated_at: datetime | None
    fetched_at: datetime
    subscribers: int | None = None
    favorites: int | None = None
    required_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    dependencies_known: bool = False


def normalize_id(value: str) -> str:
    """Only a positive uint64 ID; never allow an input URL into the HTTP client."""
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,20}", text) or not 0 < int(text) <= 2**64 - 1:
        raise ValueError("Workshop ID must be a positive uint64 decimal string")
    return str(int(text))


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed += 1
        elif not self.suppressed and tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def clean_description(value: str) -> str:
    """Produce plain text, not HTML. Preserve prose, remove executable/BBCode markup."""
    text = str(value or "")[:250_000]
    text = re.sub(
        r"\[(img|previewyoutube|youtube)(?:=[^\]]*)?\].*?\[/\1\]", "", text, flags=re.I | re.S
    )
    text = re.sub(r"\[/?(?:h[1-6]|p|list|olist|\*)(?:=[^\]]*)?\]", "\n", text, flags=re.I)
    text = re.sub(
        r"\[/?(?:b|i|u|s|strike|url|quote|code|table|tr|td|th|spoiler|noparse)(?:=[^\]]*)?\]",
        "",
        text,
        flags=re.I,
    )
    parser = _PlainText()
    parser.feed(text)
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


class _WorkshopLinks(HTMLParser):
    """Read actual anchors, not item IDs embedded in scripts or menu templates."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        parsed = urlparse(href)
        if parsed.scheme not in {"", "https"} or parsed.netloc not in {"", "steamcommunity.com"}:
            return
        if parsed.path.rstrip("/") not in {"/sharedfiles/filedetails", "/workshop/filedetails"}:
            return
        values = parse_qs(parsed.query).get("id", [])
        if len(values) != 1:
            return
        try:
            item_id = normalize_id(values[0])
        except ValueError:
            return
        if item_id not in self._seen:
            self.ids.append(item_id)
            self._seen.add(item_id)


def _timestamp(value: Any) -> datetime | None:
    try:
        value = int(value)
        return datetime.fromtimestamp(value, UTC) if value > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _preview(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and (
            host == "steamuserimages-a.akamaihd.net"
            or host.endswith(".steamusercontent.com")
            or host.endswith(".steamstatic.com")
        )
    ):
        return value
    return None


class SteamClient:
    def __init__(self, api_key: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = (api_key or "").strip() or None
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        self.last_warnings: list[str] = []
        self._discovery_warnings: list[str] = []
        self.discovery_mode: str | None = None
        self._query_metadata: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:
        if self._owned:
            await self.client.aclose()

    async def __aenter__(self) -> SteamClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    @staticmethod
    def _retry_delay(header: str | None, attempt: int) -> float:
        if header:
            try:
                delay = max(0.0, float(header))
            except ValueError:
                try:
                    when = parsedate_to_datetime(header)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    delay = max(0.0, (when - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = 0.25 * 2**attempt
            if delay > 5:
                raise SteamSourceError(
                    "Steam rate limit requests a wait beyond this bounded operation; retry the job later"
                )
            return delay
        return 0.25 * 2**attempt

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self.client.request(method, url, **kwargs)
            except httpx.RequestError:
                if attempt == 2:
                    # Exception URLs can include the API key; never return their text.
                    raise SteamSourceError("Steam request failed after bounded retries") from None
                await asyncio.sleep(0.25 * 2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(
                        self._retry_delay(response.headers.get("Retry-After"), attempt)
                    )
                    continue
            if response.status_code != 200:
                raise SteamSourceError(f"Steam returned HTTP {response.status_code}")
            if len(response.content) > 10_000_000:
                raise SteamSourceError("Steam response exceeded the metadata size budget")
            return response
        raise SteamSourceError("Steam request did not complete")  # defensive only

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request(method, API_BASE + path, **kwargs)
        try:
            payload = response.json()
        except ValueError:
            raise SteamSourceError("Steam returned malformed JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
            raise SteamSourceError("Steam response envelope is missing")
        body = payload["response"]
        if body.get("result", 1) != 1:
            raise SteamSourceError("Steam rejected the API operation")
        return body

    def _item(self, raw: dict[str, Any], expected: set[str], now: datetime) -> SteamItem | None:
        try:
            item_id = normalize_id(raw.get("publishedfileid", ""))
        except ValueError:
            self.last_warnings.append("A malformed item ID was skipped")
            return None
        if item_id not in expected:
            self.last_warnings.append(f"Unexpected item {item_id} was ignored")
            return None
        if raw.get("result") != 1:
            # EResult 9/15 can describe an unavailable individual item. Timeouts,
            # rate limits and unknown failures must fail the job, even with HTTP 200.
            # Never persist an arbitrary upstream value in an operational message.
            if raw.get("result") in (9, 15):
                self.last_warnings.append(f"Item {item_id} is unavailable or inaccessible")
                return None
            raise SteamSourceError("Steam failed to retrieve item details; retry the job later")
        if raw.get("consumer_app_id", raw.get("consumer_appid")) != APP_ID:
            self.last_warnings.append(f"Item {item_id} does not belong to WARHAMMER III")
            return None
        if raw.get("visibility", 0) != 0 or raw.get("banned", False):
            self.last_warnings.append(f"Item {item_id} is not a public available item")
            return None
        source_tags = raw.get("tags")
        source_tags = source_tags if isinstance(source_tags, list) else []
        tags = [
            clean_description(t.get("tag", ""))[:100] for t in source_tags if isinstance(t, dict)
        ]
        tags = list(dict.fromkeys(t for t in tags if t))
        query = self._query_metadata.get(item_id, {})
        file_type = raw.get("file_type", query.get("file_type"))
        if file_type is not None and file_type != 0:
            self.last_warnings.append(f"Item {item_id} is not a Mod item (file_type {file_type})")
            return None
        # The legacy details endpoint omits file_type. WH3's mod tag is used as
        # the explicit fallback classification; unknown non-Mod records are skipped.
        if file_type is None and "mod" not in {tag.lower() for tag in tags}:
            self.last_warnings.append(f"Item {item_id} has no verifiable Mod classification")
            return None
        title = clean_description(raw.get("title", ""))[:500]
        if not title:
            self.last_warnings.append(f"Item {item_id} has no title")
            return None
        children = raw.get("children", query.get("children"))
        children_source = raw if "children" in raw else query
        dependencies_known = isinstance(children, list)
        if dependencies_known and children_source.get("num_children") is not None:
            dependencies_known = _count(children_source["num_children"]) == len(children)
        required: list[str] = []
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    dependencies_known = False
                    continue
                try:
                    child_id = normalize_id(child.get("publishedfileid", ""))
                    if child_id != item_id and child_id not in required:
                        required.append(child_id)
                except ValueError:
                    dependencies_known = False
                    continue
        snapshot = dict(raw)
        snapshot["_modsage_dependencies_available"] = dependencies_known
        if query:
            snapshot["_modsage_query_metadata"] = query
        return SteamItem(
            id=item_id,
            title=title,
            description=clean_description(raw.get("description", "")),
            author=str(raw["creator"]) if raw.get("creator") else None,
            source_url=f"{COMMUNITY_BASE}/sharedfiles/filedetails/?id={item_id}",
            preview_url=_preview(raw.get("preview_url")),
            tags=tags,
            published_at=_timestamp(raw.get("time_created")),
            updated_at=_timestamp(raw.get("time_updated")),
            fetched_at=now,
            subscribers=_count(raw.get("subscriptions")),
            favorites=_count(raw.get("favorited")),
            required_ids=required,
            raw=snapshot,
            dependencies_known=dependencies_known,
        )

    async def get_details(self, ids: list[str]) -> list[SteamItem]:
        normalized = list(dict.fromkeys(normalize_id(value) for value in ids))
        if len(normalized) > 500:
            raise ValueError("A details operation accepts at most 500 IDs")
        self.last_warnings = list(self._discovery_warnings)
        items: list[SteamItem] = []
        now = datetime.now(UTC)
        for start in range(0, len(normalized), 100):
            batch = normalized[start : start + 100]
            data = {
                "itemcount": str(len(batch)),
                **{f"publishedfileids[{i}]": value for i, value in enumerate(batch)},
            }
            body = await self._json(
                "POST", "/ISteamRemoteStorage/GetPublishedFileDetails/v1/", data=data
            )
            rows = body.get("publishedfiledetails")
            if not isinstance(rows, list):
                raise SteamSourceError("Steam response is missing the item details list")
            seen: set[str] = set()
            expected = set(batch)
            for raw in rows:
                if not isinstance(raw, dict):
                    self.last_warnings.append("A malformed details record was skipped")
                    continue
                try:
                    raw_id = normalize_id(raw.get("publishedfileid", ""))
                except ValueError:
                    self.last_warnings.append("A malformed details record was skipped")
                    continue
                if raw_id in seen:
                    continue
                seen.add(raw_id)
                item = self._item(raw, expected, now)
                if item:
                    items.append(item)
            if not expected.intersection(seen):
                raise SteamSourceError("Steam omitted every requested item in a details batch")
            for missing in expected - seen:
                self.last_warnings.append(f"Steam omitted requested item {missing}")
        return items

    async def query_files(self, sort: str = "updated", limit: int = 50) -> list[str]:
        if not self.api_key:
            raise SteamConfigurationError(
                "MODSAGE_STEAM_API_KEY is required for official QueryFiles discovery"
            )
        if sort not in _SORTS or not 1 <= limit <= 500:
            raise ValueError("sort must be updated/newest/popular and limit must be 1..500")
        self.discovery_mode = "steam_web_api"
        self.last_warnings = []
        self._discovery_warnings = []
        result: list[str] = []
        cursor = "*"
        visited: set[str] = set()
        for _ in range(10):
            payload: dict[str, Any] = {
                "appid": APP_ID,
                "query_type": _SORTS[sort],
                "cursor": cursor,
                "numperpage": min(100, limit - len(result)),
                "filetype": 0,
                "return_tags": True,
                "return_children": True,
            }
            if sort == "popular":
                payload["days"] = 7
            body = await self._json(
                "GET",
                "/IPublishedFileService/QueryFiles/v1/",
                # Official header authentication avoids leaking the key through
                # ordinary HTTP client/proxy URL access logs.
                headers={"x-webapi-key": self.api_key},
                params={"input_json": json.dumps(payload)},
            )
            rows = body.get("publishedfiledetails")
            if not isinstance(rows, list):
                raise SteamSourceError("Steam QueryFiles response is missing items")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    item_id = normalize_id(row.get("publishedfileid", ""))
                except ValueError:
                    continue
                if row.get("result", 1) != 1:
                    if row.get("result") in (9, 15):
                        self._discovery_warnings.append(
                            f"Query item {item_id} is unavailable or inaccessible"
                        )
                        self.last_warnings = list(self._discovery_warnings)
                        continue
                    raise SteamSourceError(
                        "Steam failed to retrieve query items; retry the job later"
                    )
                if row.get("consumer_appid", row.get("consumer_app_id", APP_ID)) != APP_ID:
                    continue
                if row.get("file_type", 0) != 0:
                    continue
                if item_id not in result:
                    result.append(item_id)
                    self._query_metadata[item_id] = row
                if len(result) >= limit:
                    return result
            next_cursor = str(body.get("next_cursor", ""))
            if not rows or not next_cursor or next_cursor == cursor or next_cursor in visited:
                break
            visited.add(cursor)
            cursor = next_cursor
        return result

    async def discover_public(self, limit: int = 50, sort: str = "updated") -> list[str]:
        if sort not in _PUBLIC_SORTS or not 1 <= limit <= 100:
            raise ValueError(
                "Public discovery accepts sort updated/newest/popular and limit 1..100"
            )
        self.discovery_mode = "public_html_fallback"
        self._discovery_warnings = [
            "Limited public Workshop page discovery; not a complete index or authenticated API ranking"
        ]
        self.last_warnings = list(self._discovery_warnings)
        result: list[str] = []
        for page in range(1, 5):
            response = await self._request(
                "GET",
                COMMUNITY_BASE + "/workshop/browse/",
                params={
                    "appid": APP_ID,
                    "browsesort": _PUBLIC_SORTS[sort],
                    "section": "readytouseitems",
                    "numperpage": 30,
                    "p": page,
                    "l": "english",
                },
            )
            parser = _WorkshopLinks()
            parser.feed(response.text)
            if not parser.ids:
                raise SteamSourceError(
                    "Public Workshop page exposed no Mod links; source may be unavailable or its layout changed"
                )
            new_ids = [item_id for item_id in parser.ids if item_id not in result]
            if not new_ids:
                break
            result.extend(new_ids)
            if len(result) >= limit:
                return result[:limit]
        return result[:limit]

    async def get_collection_ids(self, collection_id: str) -> list[str]:
        collection_id = normalize_id(collection_id)
        self.discovery_mode = "collection"
        self._discovery_warnings = []
        self.last_warnings = []
        body = await self._json(
            "POST",
            "/ISteamRemoteStorage/GetCollectionDetails/v1/",
            data={"collectioncount": "1", "publishedfileids[0]": collection_id},
        )
        rows = body.get("collectiondetails")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise SteamSourceError("Steam collection response is missing details")
        collection = rows[0]
        if str(collection.get("publishedfileid")) != collection_id or collection.get("result") != 1:
            raise SteamSourceError("The public collection is unavailable")
        children = collection.get("children")
        if not isinstance(children, list):
            raise SteamSourceError("Steam collection response is missing members")
        if len(children) > 500:
            raise SteamSourceError("Collection exceeds the 500-item import budget")
        result: list[str] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("filetype", 0) != 0:
                self.last_warnings.append(
                    "Nested collections and non-Mod members were not imported"
                )
                continue
            try:
                item_id = normalize_id(child.get("publishedfileid", ""))
            except ValueError:
                continue
            if item_id not in result:
                result.append(item_id)
        self._discovery_warnings = list(self.last_warnings)
        return result

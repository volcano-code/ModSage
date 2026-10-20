"""Synthetic adapter fixtures; never used to populate the product catalogue."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from modsage.sources.steam import (
    SteamClient,
    SteamConfigurationError,
    SteamSourceError,
    clean_description,
    normalize_id,
)


def item(item_id="101", **overrides):
    return {
        "publishedfileid": item_id,
        "result": 1,
        "consumer_app_id": 1142710,
        "title": "Synthetic Mod fixture",
        "description": "[h1]Fixture only[/h1]A description.",
        "creator": "76561190000000000",
        "tags": [{"tag": "mod"}, {"tag": "Campaign"}],
        "time_created": 1700000000,
        "time_updated": 1700100000,
        "visibility": 0,
        "subscriptions": 7,
        "favorited": 2,
        **overrides,
    }


def details(rows):
    return httpx.Response(200, json={"response": {"result": 1, "publishedfiledetails": rows}})


@pytest.mark.asyncio
async def test_details_validates_ids_app_visibility_and_type():
    rows = [
        item("101"),
        item("102", consumer_app_id=123),
        item("103", visibility=2),
        item("104", file_type=2),
        item("105", result=9),
        item("106", tags=[]),
        item("999"),
        item("101"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: details(rows))) as http:
        source = SteamClient(client=http)
        found = await source.get_details(["101", "102", "103", "104", "105", "106", "107"])
        assert [v.id for v in found] == ["101"]
        assert found[0].subscribers == 7
        assert found[0].dependencies_known is False
        assert found[0].required_ids == []
        assert any("omitted requested item 107" in w for w in source.last_warnings)
        assert any("Unexpected item 999" in w for w in source.last_warnings)


@pytest.mark.asyncio
async def test_details_batches_and_deduplicates_requests():
    sizes = []

    def respond(request):
        form = parse_qs(request.content.decode())
        sizes.append(int(form["itemcount"][0]))
        ids = [form[f"publishedfileids[{i}]"][0] for i in range(sizes[-1])]
        return details([item(value) for value in ids])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        found = await SteamClient(client=http).get_details([str(i) for i in range(1, 151)] + ["1"])
        assert len(found) == 150
        assert sizes == [100, 50]


@pytest.mark.asyncio
async def test_query_requires_explicit_key_without_network():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("must not request"))
    ) as http:
        with pytest.raises(SteamConfigurationError, match="MODSAGE_STEAM_API_KEY"):
            await SteamClient(client=http).query_files()


@pytest.mark.asyncio
async def test_query_cursor_and_child_metadata_are_preserved(caplog):
    caplog.set_level("INFO", logger="httpx")
    calls = []

    def respond(request):
        if request.method == "POST":
            return details([item("101"), item("102")])
        assert request.headers["x-webapi-key"] == "test-key"
        assert "test-key" not in str(request.url)
        payload = json.loads(request.url.params["input_json"])
        calls.append(payload)
        rows = (
            [{"publishedfileid": "101", "file_type": 0, "children": [{"publishedfileid": "200"}]}]
            if len(calls) == 1
            else [{"publishedfileid": "102", "file_type": 0, "children": []}]
        )
        return httpx.Response(
            200, json={"response": {"publishedfiledetails": rows, "next_cursor": "next"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        source = SteamClient("test-key", http)
        ids = await source.query_files("popular", 2)
        mods = await source.get_details(ids)
        assert ids == ["101", "102"]
        assert calls[0]["days"] == 7
        assert calls[1]["cursor"] == "next"
        assert mods[0].required_ids == ["200"]
        assert mods[1].dependencies_known is True
        assert "test-key" not in caplog.text


@pytest.mark.asyncio
async def test_public_discovery_uses_anchors_not_script_text():
    html = """<script>const x='<a href="https://steamcommunity.com/sharedfiles/filedetails/?id=999">';</script>
    <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=101">a</a>
    <a href="https://evil.test/sharedfiles/filedetails/?id=666">b</a>
    <a href="/sharedfiles/filedetails/?id=102&amp;x=1">c</a>
    <a href="/sharedfiles/filedetails/?id=101">duplicate</a>"""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: (
                details([item("101"), item("102")])
                if r.method == "POST"
                else httpx.Response(200, text=html)
            )
        )
    ) as http:
        source = SteamClient(client=http)
        assert await source.discover_public(limit=2) == ["101", "102"]
        assert source.discovery_mode == "public_html_fallback"
        await source.get_details(["101", "102"])
        assert any("Limited public" in warning for warning in source.last_warnings)


@pytest.mark.asyncio
async def test_public_layout_failure_is_not_empty_success():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>Sign in</html>"))
    ) as http:
        with pytest.raises(SteamSourceError, match="exposed no Mod links"):
            await SteamClient(client=http).discover_public()


@pytest.mark.asyncio
async def test_429_retries_are_bounded_and_use_retry_after(monkeypatch):
    waits = []
    calls = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr("modsage.sources.steam.asyncio.sleep", sleep)

    def respond(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "0"})
            if len(calls) < 3
            else details([item()])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        assert len(await SteamClient(client=http).get_details(["101"])) == 1
        assert waits == [0, 0]
        assert len(calls) == 3


@pytest.mark.asyncio
async def test_long_retry_after_does_not_retry_early():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(SteamSourceError, match="retry the job later"):
            await SteamClient(client=http).get_details(["101"])
        assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"response": {}},
        {"response": {"result": 15}},
        {"response": {"publishedfiledetails": None}},
        {"response": {"publishedfiledetails": []}},
    ],
)
async def test_bad_api_envelope_is_failure(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(SteamSourceError):
            await SteamClient(client=http).get_details(["101"])


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [16, 20, 84, "provider-secret"])
async def test_item_failures_are_not_empty_success(result):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: details([item(result=result)]))
    ) as http:
        with pytest.raises(SteamSourceError) as error:
            await SteamClient(client=http).get_details(["101"])
        assert "provider-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_known_unavailable_items_are_reported_individually():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: details([item(result=9)]))
    ) as http:
        source = SteamClient(client=http)
        assert await source.get_details(["101"]) == []
        assert source.last_warnings == ["Item 101 is unavailable or inaccessible"]


@pytest.mark.asyncio
async def test_query_item_failure_is_not_empty_success():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: details([item(result=20)]))
    ) as http:
        with pytest.raises(SteamSourceError, match="query items"):
            await SteamClient("test-key", http).query_files()


@pytest.mark.asyncio
async def test_collection_members_are_not_dependencies_and_nested_collections_skipped():
    response = {
        "response": {
            "collectiondetails": [
                {
                    "publishedfileid": "500",
                    "result": 1,
                    "children": [
                        {"publishedfileid": "101", "filetype": 0},
                        {"publishedfileid": "999", "filetype": 2},
                        {"publishedfileid": "101", "filetype": 0},
                    ],
                }
            ]
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    ) as http:
        source = SteamClient(client=http)
        assert await source.get_collection_ids("500") == ["101"]
        assert source.last_warnings


@pytest.mark.asyncio
async def test_borrowed_client_is_not_closed():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: details([item()]))
    ) as http:
        source = SteamClient(client=http)
        await source.aclose()
        assert not http.is_closed


@pytest.mark.parametrize(
    "bad", ["0", "-1", "12.1", "1e9", "https://example.com/1", str(2**64), "1/../2"]
)
def test_invalid_ids(bad):
    with pytest.raises(ValueError):
        normalize_id(bad)


def test_plain_text_is_not_template_compatibility_inference():
    text = clean_description(
        "[h1]Title[/h1]<script>evil()</script>[url=https://example.com]Details[/url][img]image.png[/img]<b>incompatible with X</b>"
    )
    assert "evil()" not in text
    assert "image.png" not in text
    assert "<b>" not in text
    assert "Details" in text
    assert "incompatible with X" in text


@pytest.mark.asyncio
async def test_unsafe_preview_is_removed_and_metrics_not_invented():
    raw = item(
        preview_url="http://localhost/secret",
        subscriptions=None,
        favorited=None,
        tags=None,
        file_type=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: details([raw]))) as http:
        mod = (await SteamClient(client=http).get_details(["101"]))[0]
        assert mod.preview_url is None
        assert mod.subscribers is None
        assert mod.favorites is None


@pytest.mark.asyncio
async def test_partial_children_list_is_not_marked_complete():
    raw = item(file_type=0, children=[{"publishedfileid": "202"}], num_children=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: details([raw]))) as http:
        mod = (await SteamClient(client=http).get_details(["101"]))[0]
        assert mod.required_ids == ["202"]
        assert mod.dependencies_known is False


@pytest.mark.asyncio
async def test_transport_failure_does_not_expose_authentication_key(monkeypatch):
    async def sleep(delay):
        pass

    monkeypatch.setattr("modsage.sources.steam.asyncio.sleep", sleep)

    def fail(request):
        raise httpx.ConnectError("failed with URL ?key=sensitive-project-key", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        with pytest.raises(SteamSourceError) as error:
            await SteamClient("sensitive-project-key", http).query_files()
        assert "sensitive-project-key" not in str(error.value)
        assert error.value.__suppress_context__ is True

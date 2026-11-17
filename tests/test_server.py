import pytest

from whatthemidi.server import make_app


@pytest.fixture
async def client(aiohttp_client, db):
    return await aiohttp_client(make_app(db, static_dir=None))


async def test_stats(client, db):
    r = await client.get("/api/stats")
    assert r.status == 200
    assert (await r.json())["files"] == len(db.names)


async def test_search(client):
    r = await client.get("/api/search", params={"q": "C4 D4", "limit": "2"})
    assert r.status == 200
    data = await r.json()
    assert data["query"] == [{"midi": 60, "name": "C4"}, {"midi": 62, "name": "D4"}]
    assert len(data["results"]) == 2
    assert data["total"] >= 2


async def test_search_modes(client):
    counts = {}
    for octave in "01":
        for transpose in "01":
            r = await client.get(
                "/api/search", params={"q": "C4 D4 E4", "octave": octave, "transpose": transpose}
            )
            counts[octave, transpose] = (await r.json())["total"]
    # each option can only add matches
    assert counts["0", "0"] <= counts["1", "0"] <= counts["1", "1"]
    assert counts["0", "0"] <= counts["0", "1"] <= counts["1", "1"]


async def test_limit_is_capped(client):
    r = await client.get("/api/search", params={"q": "C4 D4", "limit": "100000"})
    assert len((await r.json())["results"]) <= 50


@pytest.mark.parametrize(
    "params, message",
    [
        ({"q": "C4 X9"}, "X9"),
        ({"q": ""}, "enter some notes"),
        ({"q": "C4", "transpose": "1"}, "at least 2 notes"),
        ({"q": "C4 " * 65}, "at most 64"),
        ({"q": "C4 D4", "limit": "abc"}, "integer"),
    ],
)
async def test_search_errors(client, params, message):
    r = await client.get("/api/search", params=params)
    assert r.status == 400
    assert message in await r.text()


async def test_context(client, db):
    doc = next(d for d in range(len(db.names)) if db.doc_length(d) >= 4)
    r = await client.get("/api/context", params={"doc": doc, "pos": 1, "len": 2, "context": 1})
    assert r.status == 200
    data = await r.json()
    assert data["file"] == db.names[doc]
    assert data["start"] == 0 and len(data["notes"]) == 4
    r = await client.get("/api/context", params={"doc": 10**6, "pos": 0})
    assert r.status == 400

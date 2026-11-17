"""aiohttp server: JSON API and the Svelte frontend (web/dist)."""

import asyncio
import time
from functools import partial
from pathlib import Path

from aiohttp import web

from .index import MAX_CONTEXT, MAX_LIMIT, MAX_QUERY_NOTES, MODES, Database
from .notes import NoteError, midi_to_name, parse_query

DB = web.AppKey("db", Database)
DEFAULT_STATIC = Path(__file__).resolve().parent.parent / "web" / "dist"


def _error(message, status=400, **extra):
    return web.json_response({"error": message, **extra}, status=status)


def _flag(request, name):
    return request.query.get(name, "0").lower() in ("1", "true", "yes", "on")


def _int(request, name, default, low, high):
    try:
        value = int(request.query.get(name, default))
    except ValueError:
        raise web.HTTPBadRequest(text=f"{name} must be an integer")
    return max(low, min(value, high))


async def _run(func, *args, **kwargs):
    # memory-mapped pages can come from the disk: do not block the event loop
    return await asyncio.get_running_loop().run_in_executor(None, partial(func, *args, **kwargs))


async def stats(request):
    db = request.app[DB]
    return web.json_response({
        "files": db.meta["files"],
        "notes": db.meta["notes"],
        "modes": list(MODES),
        "max_query_notes": MAX_QUERY_NOTES,
        "max_limit": MAX_LIMIT,
    })


async def search(request):
    db = request.app[DB]
    started = time.perf_counter()
    try:
        notes = parse_query(request.query.get("q", ""))
    except NoteError as e:
        return _error(str(e), token=e.token)
    if not notes:
        return _error("enter some notes")
    if len(notes) > MAX_QUERY_NOTES:
        return _error(f"at most {MAX_QUERY_NOTES} notes")
    octave, transpose = _flag(request, "octave"), _flag(request, "transpose")
    if transpose and len(notes) < 2:
        return _error("a search with transposition needs at least 2 notes")
    limit = _int(request, "limit", 10, 1, MAX_LIMIT)
    context = _int(request, "context", 8, 0, MAX_CONTEXT)
    result = await _run(db.search, notes, octave, transpose, limit, context)
    return web.json_response({
        "query": [{"midi": n, "name": midi_to_name(n)} for n in notes],
        **result,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    })


async def context(request):
    db = request.app[DB]
    doc = _int(request, "doc", -1, -1, 2**31)
    pos = _int(request, "pos", -1, -1, 2**31)
    length = _int(request, "len", 1, 1, MAX_QUERY_NOTES)
    ctx = _int(request, "context", 8, 0, MAX_CONTEXT)
    try:
        result = await _run(db.context, doc, pos, length, ctx)
    except ValueError as e:
        return _error(str(e))
    return web.json_response({"doc": doc, "file": db.names[doc], **result})


def make_app(db: Database, static_dir=DEFAULT_STATIC) -> web.Application:
    app = web.Application()
    app[DB] = db
    app.router.add_get("/api/stats", stats)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/context", context)

    static_dir = Path(static_dir) if static_dir else None
    if static_dir and (static_dir / "index.html").exists():
        async def index(request):
            return web.FileResponse(static_dir / "index.html")

        app.router.add_get("/", index)
        for child in static_dir.iterdir():
            if child.is_dir():
                app.router.add_static(f"/{child.name}/", child)
            elif child.name != "index.html":
                app.router.add_get(f"/{child.name}", partial(_file, child))
    else:
        async def no_frontend(request):
            return web.Response(
                text="Frontend not built: run `npm install && npm run build` in web/.\n"
                     "The API is at /api/search?q=C4+D4+E4\n"
            )

        app.router.add_get("/", no_frontend)
    return app


async def _file(path, request):
    return web.FileResponse(path)


def serve(db_dir, host="127.0.0.1", port=8080, static_dir=DEFAULT_STATIC):
    web.run_app(make_app(Database(db_dir), static_dir), host=host, port=port)

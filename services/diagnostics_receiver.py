"""Isolated, durable diagnostics receiver; run behind an HTTPS reverse proxy."""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.request import Request, urlopen

from aiohttp import web

from markcontrol.diagnostic_schema import ERRORS, ACTIVITY, clean_record

REPO = "pcblindao/mark-mouse-e-teclado-control"
PATH = "/api/mark-mouse-teclado/diagnostics"


def validate(value):
    expected = {"schema", "report_id", "version", "os", "os_release", "python", "events"}
    if not isinstance(value, dict) or set(value) != expected or type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("schema")
    for key, pattern in (("report_id", r"[a-f0-9]{64}"),
                         ("version", r"[0-9]{1,8}\.[0-9]{1,8}\.[0-9]{1,8}"),
                         ("python", r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"),
                         ("os", r"Windows|Linux|Darwin"),
                         ("os_release", r"[a-zA-Z0-9._+~-]{1,64}")):
        if not isinstance(value[key], str) or not re.fullmatch(pattern, value[key]):
            raise ValueError(key)
    rows = value["events"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise ValueError("events")
    errors = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("time")) is not int or not 0 <= row["time"] < 100000000000:
            raise ValueError("time")
        record = {key: item for key, item in row.items() if key != "time"}
        if clean_record(record) != record:
            raise ValueError("record")
        if row["event"] in ERRORS:
            errors.append(record)
    if not errors:
        activity = sorted({row["event"] for row in rows} & ACTIVITY)
        if not activity:
            raise ValueError("missing diagnostic event")
        errors = ["activity", int(time.time()) // 86400]
    signature = json.dumps([value["version"], value["os"], errors], sort_keys=True)
    return hashlib.sha256(signature.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS reports (
            signature TEXT PRIMARY KEY, payload TEXT NOT NULL, received INTEGER NOT NULL,
            last_seen INTEGER NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, issue INTEGER);
          CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, received INTEGER NOT NULL);
        """)
        if "published_at" not in {row[1] for row in self.db.execute("PRAGMA table_info(reports)")}:
            self.db.execute("ALTER TABLE reports ADD COLUMN published_at INTEGER NOT NULL DEFAULT 0")
            self.db.execute("UPDATE reports SET published_at=last_seen WHERE issue IS NOT NULL")
            self.db.commit()

    def purge(self):
        cutoff = int(time.time()) - 30 * 86400
        with self.db:
            self.db.execute("DELETE FROM receipts WHERE received < ?", (cutoff,))
            self.db.execute("DELETE FROM reports WHERE last_seen < ?", (cutoff,))

    def accept(self, payload, signature):
        now = int(time.time())
        with self.db:
            self.db.execute("DELETE FROM receipts WHERE received < ?", (now - 30 * 86400,))
            self.db.execute("DELETE FROM reports WHERE last_seen < ? AND issue IS NOT NULL", (now - 30 * 86400,))
            if self.db.execute("SELECT 1 FROM receipts WHERE id=?", (payload["report_id"],)).fetchone():
                return
            if self.db.execute("SELECT count(*) FROM receipts WHERE received > ?", (now - 86400,)).fetchone()[0] >= 2000:
                raise OverflowError("daily quota")
            count = self.db.execute("SELECT count(*) FROM reports").fetchone()[0]
            if count >= 5000:
                raise OverflowError("storage quota")
            self.db.execute("INSERT INTO receipts VALUES (?, ?)", (payload["report_id"], now))
            self.db.execute("""INSERT INTO reports(signature,payload,received,last_seen) VALUES(?,?,?,?)
                ON CONFLICT(signature) DO UPDATE SET last_seen=excluded.last_seen, occurrences=occurrences+1,
                payload=excluded.payload""",
                (signature, json.dumps(payload), now, now))


STORE = web.AppKey("store", Store)
TOKEN = web.AppKey("token", str)


def github(token, method, path, value=None):
    request = Request("https://api.github.com/repos/" + REPO + path,
                      data=json.dumps(value).encode() if value is not None else None,
                      headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                               "User-Agent": "MarkControl-Diagnostics", "Content-Type": "application/json"},
                      method=method)
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read(2_000_000))


def publish(token, signature, raw, issue_number=None, occurrences=1):
    title = "[Diagnóstico automático " + signature[:20] + "]"
    # Recover after an uncertain response from POST without creating duplicates.
    recent = [] if issue_number else github(token, "GET", "/issues?state=all&sort=created&direction=desc&per_page=100")
    for issue in recent:
        if issue.get("title") == title:
            issue_number = issue["number"]
            break
    payload = json.loads(raw)
    body = ("Diagnóstico técnico enviado automaticamente com autorização no aplicativo.\n\n"
            f"Lotes recebidos neste grupo: {occurrences}. Abaixo está o diagnóstico mais recente.\n\n```json\n" +
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n```")
    if issue_number:
        github(token, "PATCH", "/issues/" + str(issue_number), {"body": body})
        return issue_number
    issue = github(token, "POST", "/issues", {"title": title, "body": body})
    return issue["number"]


async def worker(app):
    store, token = app[STORE], app[TOKEN]
    try:
        while True:
            store.purge()
            row = store.db.execute("SELECT signature,payload,issue,occurrences,last_seen FROM reports WHERE issue IS NULL OR published_at < last_seen ORDER BY received LIMIT 1").fetchone()
            if row and token:
                try:
                    issue = await asyncio.to_thread(publish, token, *row[:4])
                    with store.db:
                        store.db.execute("UPDATE reports SET issue=?,published_at=? WHERE signature=?", (issue, row[4], row[0]))
                except Exception as exc:
                    # No payloads, token or exception messages in service logs.
                    print("github_delivery_failed " + type(exc).__name__, flush=True)
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        return


def create_app(path, token):
    app = web.Application(client_max_size=64000)
    app[STORE], app[TOKEN] = Store(path), token
    limits = defaultdict(deque)

    async def receive(request):
        now = time.monotonic()
        # The service listens on loopback; only the trusted local proxy sets X-Real-IP.
        ip = request.headers.get("X-Real-IP", request.remote or "unknown")
        if len(limits) >= 10000 and ip not in limits:
            for key in list(limits):
                if not limits[key] or now - limits[key][-1] > 3600:
                    del limits[key]
            if len(limits) >= 10000:
                raise web.HTTPTooManyRequests()
        queue = limits[ip]
        while queue and now - queue[0] > 3600:
            queue.popleft()
        if len(queue) >= 20:
            raise web.HTTPTooManyRequests()
        queue.append(now)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            payload = await request.json()
            signature = validate(payload)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise web.HTTPBadRequest() from None
        try:
            app[STORE].accept(payload, signature)
        except OverflowError:
            raise web.HTTPTooManyRequests() from None
        return web.json_response({"accepted": payload["report_id"]}, status=202)

    async def health(request):
        return web.json_response({"status": "ok", "github_configured": bool(token), "receiver_schema": 2})

    async def lifecycle(app):
        task = asyncio.create_task(worker(app))
        yield
        task.cancel()
        await task
        app[STORE].db.close()

    app.router.add_post(PATH, receive)
    app.router.add_get(PATH + "/health", health)
    app.cleanup_ctx.append(lifecycle)
    return app


if __name__ == "__main__":
    credential = Path(os.environ.get("CREDENTIALS_DIRECTORY", "/run/secrets")) / "github-token"
    token = credential.read_text().strip()
    if not token:
        raise RuntimeError("GitHub credential missing")
    app = create_app(os.environ.get("DIAGNOSTICS_DB", "/var/lib/markcontrol-diagnostics/reports.sqlite"), token)
    web.run_app(app, host=os.environ.get("DIAGNOSTICS_HOST", "127.0.0.1"), port=47805, access_log=None, print=None)

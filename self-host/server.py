"""WSGI application for publishing and reading independent artifact snapshots."""

import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import stat
import xml.etree.ElementTree as ET
import zipfile
import zlib
from contextlib import closing
from html import escape
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlsplit

from snapshot import LOCATOR, MAX_BYTES, MAX_FILES, valid_path

PUSH = re.compile(rf"/api/artifacts/([a-f0-9]{{64}}/({LOCATOR.pattern}))")
SHARE = re.compile(r"/a/([a-f0-9]{32})/(.*)")
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
HTML_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "ul",
    "ol",
    "li",
    "b",
    "i",
    "em",
    "strong",
    "del",
    "s",
    "code",
    "pre",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "details",
    "summary",
    "a",
    "img",
    "br",
    "hr",
}


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def save(db, target, locator, payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle, db:
        entries = bundle.infolist()
        if not 0 < len(entries) <= MAX_FILES or sum(i.file_size for i in entries) > MAX_BYTES:
            raise RequestError(413, "Snapshot exceeds the file or size limit")
        names = set()
        for entry in entries:
            name = entry.orig_filename
            if (
                not valid_path(name)
                or name in names
                or entry.flag_bits & 1
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
            ):
                raise RequestError(400, "Snapshot contains an unsupported file")
            names.add(name)
        db.execute(
            "INSERT INTO shares(target, locator, id) VALUES (?, ?, ?) "
            "ON CONFLICT(target) DO NOTHING",
            (target, locator, secrets.token_hex(16)),
        )
        (share,) = db.execute("SELECT id FROM shares WHERE target = ?", (target,)).fetchone()
        db.execute("DELETE FROM files WHERE share = ?", (share,))
        for entry in entries:
            with bundle.open(entry) as member:
                data = member.read(entry.file_size + 1)
            if len(data) != entry.file_size:
                raise RequestError(400, "Incomplete ZIP member")
            db.execute(
                "INSERT INTO files(share, name, data) VALUES (?, ?, ?)",
                (share, entry.filename, data),
            )
    return share


def document_preview(source):
    # LiteXML's common text blocks also exist in HTML. Only emit those tags and safe links.
    if len(source) > MAX_PREVIEW_BYTES or b"\0" in source or b"<!DOCTYPE" in source.upper():
        return ""
    try:
        root = ET.fromstring(source)
        for node in root.iter():
            if node.tag not in HTML_TAGS:
                node.tag = "pre" if node.tag == "codeblock" else "div"
            original = node.attrib.copy()
            node.attrib.clear()
            if original.get("id"):
                node.set("id", original["id"])
            attr = {"a": "href", "img": "src"}.get(node.tag)
            if attr and original.get(attr):
                dest = original[attr].strip()
                url = urlsplit(dest)
                relative = not url.scheme and not url.netloc and not dest.startswith(("/", "\\"))
                web_link = node.tag == "a" and url.scheme in ("http", "https")
                if relative or web_link:
                    node.set(attr, dest)
            if node.tag == "img":
                node.set("alt", original.get("alt", ""))
        return ET.tostring(root, encoding="unicode", method="html")
    except (ET.ParseError, LookupError, ValueError, RecursionError):
        return ""


def artboard_preview(source, names):
    if len(source) > MAX_PREVIEW_BYTES:
        return ""
    try:
        canvas = json.loads(source)
    except (ValueError, RecursionError):
        return ""
    if not isinstance(canvas, dict) or not isinstance(canvas.get("boards"), dict):
        return ""
    frames = []
    for board_id, board in canvas["boards"].items():
        file = board.get("file") if isinstance(board, dict) else None
        if (
            not isinstance(file, str)
            or file not in names
            or not file.lower().endswith((".html", ".htm"))
        ):
            continue
        href = "./" + quote(file)
        title = escape(board["title"] if isinstance(board.get("title"), str) else board_id)
        frames.append(
            f'<section><h2><a href="{href}">{title}</a></h2>'
            f'<iframe title="{title}" src="{href}" loading="lazy" sandbox="allow-scripts">'
            "</iframe></section>"
        )
    return "".join(frames)


def read_share(db, share, name):
    record = db.execute("SELECT locator FROM shares WHERE id = ?", (share,)).fetchone()
    if not record:
        raise RequestError(404, "Share not found")
    locator = record[0]
    kind = locator.split("/")[0]
    filename = name or ("index.html" if kind in {"html", "slides"} else None)
    if filename:
        row = db.execute(
            "SELECT data FROM files WHERE share = ? AND name = ?", (share, filename)
        ).fetchone()
        if row is not None:
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            if mime.startswith("text/"):
                mime += "; charset=utf-8"
            return mime, row[0]
        if name:
            raise RequestError(404, "File not found")

    names = [
        r[0] for r in db.execute("SELECT name FROM files WHERE share = ? ORDER BY name", (share,))
    ]
    entry = {"artboard": "canvas.json", "document": "doc.xml", "verify": "report.xml"}.get(kind)
    preview = ""
    if entry in names:
        (source,) = db.execute(
            "SELECT data FROM files WHERE share = ? AND name = ?", (share, entry)
        ).fetchone()
        preview = (
            artboard_preview(source, set(names)) if kind == "artboard" else document_preview(source)
        )
    links = "".join(f'<li><a href="./{quote(n)}">{escape(n)}</a></li>' for n in names)
    listing = f"<ul>{links}</ul>"
    if preview:
        listing = f"<details><summary>Source files</summary>{listing}</details>"
    title = escape(locator)
    page = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>{title}</title><style>body{{max-width:72ch;margin:3rem auto;padding:0 1rem;"
        "font:1rem/1.55 system-ui,sans-serif;overflow-wrap:anywhere} "
        "pre{white-space:pre-wrap} img{max-width:100%} "
        "iframe{width:100%;height:30rem;border:1px solid #bbb;box-sizing:border-box}</style>"
        f"<main>{preview or f'<h1>{title}</h1>'}{listing}</main>"
    )
    return "text/html; charset=utf-8", page.encode()


def create_app(database=None, token=None):
    token = token or os.environ.get("RYUGI_SELF_HOST_TOKEN")
    if not token:
        raise ValueError("Set RYUGI_SELF_HOST_TOKEN before starting the server")
    credential = hashlib.sha256(f"Bearer {token}".encode()).digest()
    database = Path(database or os.environ.get("RYUGI_SELF_HOST_DB", "shares.sqlite3"))
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS shares (target TEXT PRIMARY KEY, locator TEXT NOT NULL, id TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS files (share TEXT, name TEXT, data BLOB NOT NULL, PRIMARY KEY(share, name));
        """)

    def dispatch(env):
        method = env["REQUEST_METHOD"]
        path = env["PATH_INFO"].encode("latin-1").decode("utf-8")
        match = PUSH.fullmatch(path)
        if method == "PUT" and match:
            supplied = hashlib.sha256(env.get("HTTP_AUTHORIZATION", "").encode()).digest()
            if not hmac.compare_digest(supplied, credential):
                raise RequestError(401, "Invalid publishing token")
            if env.get("CONTENT_TYPE") != "application/zip":
                raise RequestError(415, "Expected application/zip")
            length = env.get("CONTENT_LENGTH", "")
            if not length.isdecimal():
                raise RequestError(411, "Content-Length required")
            if int(length) > MAX_BYTES:
                raise RequestError(413, "ZIP exceeds 64 MiB")
            data = env["wsgi.input"].read(int(length))
            if len(data) != int(length):
                raise RequestError(400, "Incomplete upload")
            with closing(sqlite3.connect(database, timeout=30)) as db:
                share = save(db, *match.groups(), data)
            return "application/json", json.dumps({"path": f"/a/{share}/"}).encode()
        if method in {"GET", "HEAD"}:
            if path == "/":
                return "text/plain", b"Artifact snapshot server\n"
            match = SHARE.fullmatch(path)
            if match:
                with closing(sqlite3.connect(database)) as db:
                    db.execute("BEGIN")
                    return read_share(db, *match.groups())
        raise RequestError(404, "Not found")

    def application(env, start_response):
        status = HTTPStatus.OK
        try:
            mime, body = dispatch(env)
        except RequestError as error:
            status, mime, body = HTTPStatus(error.status), "text/plain", str(error).encode()
        except (zipfile.BadZipFile, UnicodeError, zlib.error, EOFError, NotImplementedError):
            status, mime, body = HTTPStatus.BAD_REQUEST, "text/plain", b"Invalid snapshot or path"
        headers = [
            ("Content-Type", mime),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
            (
                "Content-Security-Policy",
                (
                    "sandbox allow-scripts; default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                    "worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
                ),
            ),
        ]
        if status == HTTPStatus.OK and env["REQUEST_METHOD"] in {"GET", "HEAD"}:
            # A sandboxed HTML snapshot can fetch its own relative assets using its share URL.
            headers.append(("Access-Control-Allow-Origin", "*"))
        start_response(f"{status.value} {status.phrase}", headers)
        return [] if env["REQUEST_METHOD"] == "HEAD" else [body]

    return application

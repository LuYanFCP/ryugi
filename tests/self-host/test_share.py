import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

TOOL = Path(__file__).resolve().parents[2] / "self-host"
sys.path.insert(0, str(TOOL))
from server import create_app


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class ShareTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / "my project"
        self.artifact = self.project / ".ryugi/artifacts/html/demo"
        self.artifact.mkdir(parents=True)
        self.db = Path(temp.name) / "shares.sqlite"
        self.token = "integration-test-publish-token"
        self.http = make_server(
            "127.0.0.1", 0, create_app(self.db, self.token), handler_class=QuietHandler
        )
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.origin = f"http://127.0.0.1:{self.http.server_port}"
        self.open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def push(self, locator="html/demo", token=None, success=True, extra=()):
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL / "push.py"),
                "--server",
                self.origin,
                "--project-dir",
                str(self.project),
                "--artifact",
                locator,
                *extra,
            ],
            env={**os.environ, "RYUGI_SELF_HOST_TOKEN": token or self.token},
            check=False,
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        self.assertNotEqual(result.returncode, 0)
        return result.stderr.strip()

    def test_push_read_and_replace(self):
        (self.artifact / "index.html").write_text('<img src="assets/pic.png">')
        assets = self.artifact / "assets"
        assets.mkdir()
        (assets / "pic.png").write_bytes(b"\x00\xff")
        (self.artifact / "comments.json").write_text("private comment")
        (self.artifact / ".state").mkdir()
        (self.artifact / ".state/key").write_text("private key")
        url = self.push()
        with self.open(url) as page:
            self.assertEqual(page.read(), b'<img src="assets/pic.png">')
            self.assertIn("sandbox", page.headers["Content-Security-Policy"])
        with self.open(url + "assets/pic.png") as image:
            self.assertEqual(image.read(), b"\x00\xff")
        (assets / "pic.png").unlink()
        (self.artifact / "index.html").write_text("changed")
        self.assertEqual(self.push(), url)
        self.assertEqual(self.open(url).read(), b"changed")
        for path in ("assets/pic.png", "comments.json", ".state/key"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.open(url + path)
            self.assertEqual(error.exception.code, 404)

    def test_auth_and_unsafe_sources(self):
        (self.artifact / "index.html").write_text("share")
        self.assertIn("401", self.push(token="wrong", success=False))
        outside = self.project / "private"
        outside.write_text("private")
        (self.artifact / "link").symlink_to(outside)
        self.push(success=False)
        self.push("html/../../private", success=False)
        self.assertIn(
            "HTTPS", self.push(success=False, extra=("--server", "http://shares.invalid"))
        )
        (self.artifact.parent / "alias").symlink_to(self.artifact, target_is_directory=True)
        self.push("html/alias", success=False)

    def test_document_preview_and_native_sources(self):
        directory = self.artifact.parents[1] / "document/spec"
        directory.mkdir(parents=True)
        source = (
            "<doc><h1>设计</h1><p>Hello &amp; bye</p>"
            '<a href="javascript:bad()">link</a><script>bad()</script></doc>'
        )
        (directory / "doc.xml").write_text(source)
        (directory / "图 #1.txt").write_text("asset")
        url = self.push("document/spec")
        page = self.open(url).read().decode()
        self.assertIn("<h1>设计</h1>", page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("<script>", page)
        self.assertEqual(self.open(url + "doc.xml").read().decode(), source)
        name = urllib.parse.quote("图 #1.txt")
        self.assertIn(name, page)
        self.assertEqual(self.open(url + name).read(), b"asset")

    def test_shared_namespace_across_checkouts(self):
        (self.artifact / "index.html").write_text("first")
        url = self.push(extra=("--namespace", "team/project"))
        self.project = self.project.parent / "other checkout"
        directory = self.project / ".ryugi/artifacts/html/demo"
        directory.mkdir(parents=True)
        (directory / "index.html").write_text("second")
        self.assertNotEqual(self.push(), url)
        self.assertEqual(self.push(extra=("--namespace", "team/project")), url)
        self.assertEqual(self.open(url).read(), b"second")

    def test_artboard_previews_its_own_html(self):
        board = self.artifact.parents[1] / "artboard/options"
        (board / "pages").mkdir(parents=True)
        (board / "pages/a b.html").write_text("Option A")
        (board / "canvas.json").write_text(
            json.dumps(
                {
                    "boards": {
                        "a": {"file": "pages/a b.html", "title": "A & B"},
                        "b": {"file": "https://example.org/foreign.html"},
                    }
                }
            )
        )
        url = self.push("artboard/options")
        page = self.open(url).read().decode()
        self.assertIn('src="./pages/a%20b.html"', page)
        self.assertIn("A &amp; B", page)
        self.assertNotIn("foreign.html", page)
        self.assertEqual(self.open(url + "pages/a%20b.html").read(), b"Option A")

    def test_invalid_replacement_preserves_previous_files(self):
        namespace = "a" * 64
        endpoint = self.origin + f"/api/artifacts/{namespace}/html/test"

        def put(files, corrupt=False):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                for name, data in files:
                    z.writestr(name, data)
            body = archive.getvalue()
            if corrupt:
                body = body.replace(b"bad-crc", b"changed", 1)
            return self.open(
                urllib.request.Request(
                    endpoint,
                    data=body,
                    method="PUT",
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "Content-Type": "application/zip",
                    },
                )
            )

        stable = self.origin + json.load(put([("index.html", "original")]))["path"]
        for bad in ("../outside", "a/.env", "comments.json", "C:/private"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                put([("index.html", "partial"), (bad, "bad")])
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            put([("index.html", "partial"), ("data.txt", "bad-crc")], corrupt=True)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.open(stable).read(), b"original")


if __name__ == "__main__":
    unittest.main()

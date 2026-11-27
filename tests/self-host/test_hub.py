import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server
from wsgiref.util import setup_testing_defaults

TOOL = Path(__file__).resolve().parents[2] / "self-host"
sys.path.insert(0, str(TOOL))
from hub import Gateway, create_hub
from push import publish
from server import create_app

ORIGIN = "http://127.0.0.1:8788"


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class HubTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.project = root / "project"
        self.folder = self.project / ".ryugi/artifacts/document/spec"
        self.folder.mkdir(parents=True)
        self.browser_id = "1aB234567890123456789"
        (self.folder / ".artifact-id").write_text(self.browser_id + "\n")
        (self.folder / "doc.xml").write_text("<doc><h1>Published</h1></doc>")
        self.token = "test-remote-publisher-token"
        tokens = patch.dict(os.environ, RYUGI_SELF_HOST_TOKEN=self.token)
        tokens.start()
        self.addCleanup(tokens.stop)
        http = make_server(
            "127.0.0.1", 0, create_app(root / "db", self.token), handler_class=QuietHandler
        )
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.server = f"http://127.0.0.1:{http.server_port}"
        self.app, self.key = create_hub([self.project], self.server)

    def request(
        self, path, method="GET", data=None, key=True, host="127.0.0.1:8788", origin=None, cookie=""
    ):
        env = {}
        setup_testing_defaults(env)
        body = json.dumps(data).encode() if data is not None else b""
        env.update(
            PATH_INFO=path,
            REQUEST_METHOD=method,
            HTTP_HOST=host,
            SERVER_PORT="8788",
            CONTENT_TYPE="application/json",
            CONTENT_LENGTH=str(len(body)),
            HTTP_COOKIE=cookie,
        )
        env["wsgi.input"] = io.BytesIO(body)
        if key:
            env["HTTP_X_HUB_SESSION"] = self.key
        if origin:
            env["HTTP_ORIGIN"] = origin
        result = {}

        def start(status, headers):
            result["status"] = int(status.split()[0])

        result["body"] = b"".join(self.app(env, start))
        return result

    def test_browser_lists_one_project_and_publishes_a_readable_stable_link(self):
        self.assertEqual(self.request("/", key=False)["status"], 200)
        listed = json.loads(self.request("/api/artifacts")["body"])
        self.assertEqual(listed["projects"], [str(self.project)])
        self.assertEqual(
            listed["items"], [{"project": 0, "artifact": "document/spec", "id": self.browser_id}]
        )
        self.assertNotIn(self.token, str(listed))

        def share():
            result = self.request(
                "/api/share", "POST", {"project": 0, "artifact": "document/spec"}, origin=ORIGIN
            )
            self.assertEqual(result["status"], 200, result["body"])
            return json.loads(result["body"])["url"]

        url = share()
        self.assertEqual(share(), url)
        self.assertEqual(publish(self.project, "document/spec", self.server), url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url) as reader:
            self.assertIn(b"<h1>Published</h1>", reader.read())

    def test_unpaired_and_cross_origin_requests_cannot_list_or_publish(self):
        target = {"project": 0, "artifact": "document/spec"}
        self.assertEqual(self.request("/api/artifacts", key=False)["status"], 403)
        self.assertEqual(
            self.request("/api/artifacts", host="attacker.invalid:8788")["status"], 403
        )
        for origin in (None, "null", "https://attacker.invalid"):
            self.assertEqual(
                self.request("/api/share", "POST", target, origin=origin)["status"], 403
            )
        self.assertEqual(
            self.request("/api/share", "POST", target, key=False, origin=ORIGIN)["status"], 403
        )
        self.assertNotEqual(self.request("/api/share")["status"], 200)

    def test_migrated_project_keeps_browser_id_and_original_share_path(self):
        target = {"project": 0, "artifact": "document/spec"}
        old_url = publish(self.project, target["artifact"], self.server)
        (self.project / ".ryugi").rename(self.project / ".artideck")
        inventory = json.loads(self.request("/api/artifacts")["body"])
        self.assertEqual([item["id"] for item in inventory["items"]], [self.browser_id])
        with patch.dict(
            os.environ, RYUGI_SELF_HOST_PUBLIC_URL="http://gateway.example.test/prefix/"
        ):
            result = self.request("/api/share", "POST", target, origin=ORIGIN)
        self.assertEqual(result["status"], 200, result["body"])
        self.assertEqual(
            json.loads(result["body"])["url"],
            "http://gateway.example.test/prefix" + old_url.removeprefix(self.server),
        )

    def test_symlinked_artideck_root_cannot_be_shared(self):
        (self.project / ".artideck").symlink_to(self.project / ".ryugi", target_is_directory=True)
        self.assertEqual(json.loads(self.request("/api/artifacts")["body"])["items"], [])
        with self.assertRaisesRegex(ValueError, "without symlinks"):
            publish(self.project, "document/spec", self.server)

    def test_local_authorization_expires_when_helper_restarts(self):
        self.app, new_key = create_hub([self.project], self.server)
        self.assertNotEqual(self.key, new_key)
        self.assertEqual(self.request("/api/artifacts")["status"], 403)
        self.key = new_key
        self.assertEqual(self.request("/api/artifacts")["status"], 200)

    def test_gateway_home_and_publication_use_the_existing_native_session(self):
        host = "hub.example.test:7892"
        origin = "http://" + host
        key_file = self.project.parent / "native-key"
        key_file.write_text("a" * 48 + "\n")
        cookie = "ryugi_artifacts_7892=" + "a" * 48
        gateway = Gateway(origin, key_file, "ryugi_artifacts_7892")
        self.app, self.key = create_hub([self.project], self.server, gateway=gateway)
        home = self.request("/", host=host, key=False)
        self.assertEqual(home["status"], 200)
        self.assertIn(b'type="password"', home["body"])
        self.assertNotIn(b"a" * 48, home["body"])
        self.assertEqual(self.request("/", "HEAD", host=host), {"status": 200, "body": b""})
        endpoint = "/_share/api/artifacts"
        self.assertEqual(self.request(endpoint, host=host)["status"], 403)
        self.assertEqual(
            self.request(endpoint, host=host, cookie="other=" + "a" * 48)["status"], 403
        )
        self.assertEqual(self.request(endpoint, host=host, cookie=cookie, key=False)["status"], 200)
        self.assertEqual(
            self.request(endpoint, host="attacker.invalid", cookie=cookie)["status"], 403
        )
        self.assertEqual(self.request("/api/artifacts", host=host, cookie=cookie)["status"], 404)
        target = {"project": 0, "artifact": "document/spec"}
        for unsafe_origin in (None, "null", "http://attacker.invalid"):
            self.assertEqual(
                self.request(
                    "/_share/api/share",
                    "POST",
                    target,
                    host=host,
                    cookie=cookie,
                    origin=unsafe_origin,
                )["status"],
                403,
            )
        posted = self.request(
            "/_share/api/share", "POST", target, host=host, cookie=cookie, origin=origin, key=False
        )
        self.assertEqual(posted["status"], 200, posted["body"])
        key_file.write_text("b" * 48)
        self.assertEqual(self.request(endpoint, host=host, cookie=cookie)["status"], 403)

    def test_cannot_select_a_local_path_or_change_destination_from_the_browser(self):
        for data in [
            {"project": str(self.project), "artifact": "document/spec"},
            {"project": -1, "artifact": "document/spec"},
            {"project": 0, "artifact": "../outside"},
            {"project": 0, "artifact": "document/spec", "server": "https://other.invalid"},
        ]:
            self.assertEqual(self.request("/api/share", "POST", data, origin=ORIGIN)["status"], 400)
        (self.folder.parent / "alias").symlink_to(self.folder, target_is_directory=True)
        (self.folder / ".artifact-id").unlink()
        (self.folder / ".artifact-id").symlink_to(self.folder / "doc.xml")
        self.assertEqual(
            json.loads(self.request("/api/artifacts")["body"])["items"],
            [{"project": 0, "artifact": "document/spec", "id": None}],
        )

    def test_project_replaced_by_a_symlink_cannot_be_shared(self):
        other_project = self.project.with_name("other")
        self.project.rename(other_project)
        self.project.symlink_to(other_project, target_is_directory=True)
        self.assertEqual(json.loads(self.request("/api/artifacts")["body"])["items"], [])
        target = {"project": 0, "artifact": "document/spec"}
        self.assertEqual(self.request("/api/share", "POST", target, origin=ORIGIN)["status"], 409)

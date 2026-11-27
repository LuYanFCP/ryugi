"""Share artifacts from selected projects using a browser on this workstation."""

import argparse
import http.client
import json
import os
import re
import secrets
import stat
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from push import artifact_root, destination_url, publish, publishing_token
from snapshot import ARTIFACT_TYPES, LOCATOR

HOST = "127.0.0.1"
PORT = 8788
ASSETS = Path(__file__).resolve().parent
HUB_ID = re.compile(rb"[a-zA-Z0-9_-]{8,64}")
GATEWAY_PATH = "/_share/"


class Gateway:
    """Accept the native Hub cookie only at the configured browser origin."""

    def __init__(self, origin, key_file, cookie_name):
        url = destination_url(origin, for_readers=True)
        if url.path not in ("", "/") or not re.fullmatch(r"[A-Za-z0-9_-]+", cookie_name):
            raise ValueError("Provide the gateway origin and the native Hub cookie name")
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        default_port = 443 if url.scheme == "https" else 80
        self.host = f"{host}:{url.port}" if url.port and url.port != default_port else host
        self.origin = f"{url.scheme}://{self.host}"
        self.cookie_name = cookie_name
        self.key_file = key_file
        if not self.read_key():
            raise ValueError("The native Hub key file is empty or invalid")

    def read_key(self):
        with self.key_file.open("rb") as source:
            raw = source.read(513)
        key = raw.rstrip(b"\r\n")
        return key if len(raw) <= 512 and re.fullmatch(rb"[A-Za-z0-9._~-]+", key) else b""

    def authorized(self, cookie):
        try:
            jar = SimpleCookie()
            jar.load(cookie)
            entry = jar.get(self.cookie_name)
            if not entry or not entry.value or not entry.value.isascii():
                return False
            return secrets.compare_digest(entry.value.encode("ascii"), self.read_key())
        except (CookieError, OSError):
            return False


def artifact_id(folder):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    path = folder / ".artifact-id"
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        with os.fdopen(os.open(path, flags), "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return None
            data = source.read(67)
    except OSError:
        return None
    value = data.rstrip(b"\r\n")
    if len(data) < 67 and HUB_ID.fullmatch(value):
        return value.decode("ascii")
    return None


def list_artifacts(projects):
    items = []
    for index, project in enumerate(projects):
        root = artifact_root(project)
        if not root.is_dir() or root.resolve() != root:
            continue
        for kind in ARTIFACT_TYPES:
            group = root / kind
            if group.is_symlink() or not group.is_dir():
                continue
            for folder in sorted(group.iterdir()):
                locator = f"{kind}/{folder.name}"
                if LOCATOR.fullmatch(locator) and not folder.is_symlink() and folder.is_dir():
                    items.append({"project": index, "artifact": locator, "id": artifact_id(folder)})
    return items


def create_hub(project_dirs, server, namespace=None, *, gateway=None):
    server = destination_url(server).geturl().rstrip("/")
    publishing_token()
    projects = list(dict.fromkeys(path.resolve(strict=True) for path in project_dirs))
    if not projects or not all(path.is_dir() for path in projects):
        raise ValueError("Provide a project directory with --project-dir")
    if namespace and len(projects) > 1:
        raise ValueError("Use --namespace with a single project directory")
    key = secrets.token_urlsafe(32)
    assets = {
        "/": ("text/html", (ASSETS / "hub.html").read_bytes()),
        "/hub.js": ("text/javascript", (ASSETS / "hub.js").read_bytes()),
        "/hub.css": ("text/css", (ASSETS / "hub.css").read_bytes()),
    }
    if gateway:
        landing = (ASSETS / "gateway.html").read_bytes()
        gateway_assets = {
            "/gateway.js": ("text/javascript", (ASSETS / "gateway.js").read_bytes()),
            "/gateway.css": ("text/css", (ASSETS / "gateway.css").read_bytes()),
        }
        reader_base = (
            destination_url(
                os.environ.get("RYUGI_SELF_HOST_PUBLIC_URL") or server, for_readers=True
            )
            .geturl()
            .rstrip("/")
        )

    def app(env, start_response):
        def respond(status, data, content_type="application/json"):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            start_response(
                status,
                [
                    ("Content-Type", content_type + "; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                    ("Referrer-Policy", "no-referrer"),
                    ("X-Content-Type-Options", "nosniff"),
                    ("X-Frame-Options", "DENY"),
                    (
                        "Content-Security-Policy",
                        "default-src 'none'; style-src 'self'; "
                        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                        "form-action 'none'; base-uri 'none'",
                    ),
                ],
            )
            return [] if env["REQUEST_METHOD"] == "HEAD" else [body]

        host = env.get("HTTP_HOST", "").lower()
        local_host = f"{HOST}:{env['SERVER_PORT']}"
        via_gateway = gateway is not None and host == gateway.host
        expected_origin = gateway.origin if via_gateway else f"http://{local_host}"
        origin = env.get("HTTP_ORIGIN")
        if (not via_gateway and host != local_host) or (
            origin is not None and origin != expected_origin
        ):
            return respond("403 Forbidden", {"error": "请从已配置的 Hub 地址访问。"})

        path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
        if via_gateway:
            if path == "/" and method in {"GET", "HEAD"}:
                return respond("200 OK", landing, "text/html")
            if not path.startswith(GATEWAY_PATH):
                return respond("404 Not Found", {"error": "页面不存在。"})
            path = "/" + path.removeprefix(GATEWAY_PATH)
            if method in {"GET", "HEAD"} and path in gateway_assets:
                content_type, data = gateway_assets[path]
                return respond("200 OK", data, content_type)
        if method in {"GET", "HEAD"} and path in assets:
            content_type, data = assets[path]
            return respond("200 OK", data, content_type)
        if path not in {"/api/artifacts", "/api/share"}:
            return respond("404 Not Found", {"error": "页面不存在。"})
        if via_gateway:
            if not gateway.authorized(env.get("HTTP_COOKIE", "")):
                return respond("403 Forbidden", {"error": "请在 Hub 首页使用原访问密钥登录。"})
        else:
            supplied = env.get("HTTP_X_HUB_SESSION", "")
            if not supplied.isascii() or not secrets.compare_digest(supplied, key):
                return respond("403 Forbidden", {"error": "请先打开启动命令打印的本机授权链接。"})
        if method == "GET" and path == "/api/artifacts":
            try:
                items = list_artifacts(projects)
            except OSError:
                return respond(
                    "500 Internal Server Error", {"error": "无法读取项目目录，请检查本机权限。"}
                )
            return respond(
                "200 OK",
                {
                    "server": reader_base if via_gateway else server,
                    "projects": list(map(str, projects)),
                    "items": items,
                },
            )
        if method != "POST" or path != "/api/share":
            return respond("405 Method Not Allowed", {"error": "此接口不支持该请求方式。"})
        if origin != expected_origin:
            return respond("403 Forbidden", {"error": "请从 Hub 的分享页面发布。"})
        length = env.get("CONTENT_LENGTH", "")
        if (
            env.get("CONTENT_TYPE", "").split(";")[0] != "application/json"
            or not re.fullmatch(r"[0-9]{1,4}", length)
            or int(length) > 2048
        ):
            return respond("400 Bad Request", {"error": "分享请求格式无效。"})
        try:
            data = json.loads(env["wsgi.input"].read(int(length)))
        except (ValueError, RecursionError):
            return respond("400 Bad Request", {"error": "分享请求格式无效。"})
        if (
            not isinstance(data, dict)
            or data.keys() != {"project", "artifact"}
            or type(data["project"]) is not int
            or not 0 <= data["project"] < len(projects)
            or not isinstance(data["artifact"], str)
            or not LOCATOR.fullmatch(data["artifact"])
        ):
            return respond("400 Bad Request", {"error": "请选择已配置项目中的产物。"})
        try:
            project = projects[data["project"]]
            if project.resolve(strict=True) != project:
                return respond("409 Conflict", {"error": "项目目录已移动，请重新启动分享工具。"})
            url = publish(project, data["artifact"], server, namespace)
        except (OSError, ValueError) as error:
            return respond("502 Bad Gateway", {"error": f"分享失败：{error}"})
        except http.client.HTTPException:
            return respond(
                "502 Bad Gateway", {"error": "接收端的 HTTP 响应无效，请检查自部署地址。"}
            )
        return respond("200 OK", {"url": url})

    return app, key


class LocalServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-dir", type=Path, action="append", help="project root (repeatable, default: cwd)"
    )
    parser.add_argument(
        "--server", default=os.environ.get("RYUGI_SELF_HOST_URL"), help="snapshot server base URL"
    )
    parser.add_argument("--namespace", help="stable name for a single project, as in push.py")
    parser.add_argument(
        "--gateway-origin", help="browser origin of the reverse proxy serving the Hub"
    )
    parser.add_argument(
        "--hub-key-file", type=Path, help="native Hub key file used to verify its session"
    )
    parser.add_argument("--hub-cookie", help="name of the native Hub's HttpOnly cookie")
    args = parser.parse_args()
    try:
        gateway = None
        if any((args.gateway_origin, args.hub_key_file, args.hub_cookie)):
            if not all((args.gateway_origin, args.hub_key_file, args.hub_cookie)):
                parser.error(
                    "--gateway-origin, --hub-key-file and --hub-cookie must be given together"
                )
            gateway = Gateway(args.gateway_origin, args.hub_key_file, args.hub_cookie)
        app, key = create_hub(
            args.project_dir or [Path.cwd()], args.server, args.namespace, gateway=gateway
        )
        with make_server(HOST, PORT, app, server_class=LocalServer) as http:
            print(
                f"在本机浏览器打开此链接以启用分享：http://{HOST}:{PORT}/#session={key}", flush=True
            )
            http.serve_forever()
    except (ValueError, OSError) as error:
        parser.exit(1, f"Hub share: {error}\n")
    except KeyboardInterrupt:
        pass

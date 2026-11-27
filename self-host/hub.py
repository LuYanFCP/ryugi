"""Share artifacts from selected projects using a browser on this workstation."""

import argparse
import http.client
import json
import os
import re
import secrets
import stat
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from push import destination_url, publish, publishing_token
from snapshot import ARTIFACT_TYPES, LOCATOR

HOST = "127.0.0.1"
PORT = 8788
ASSETS = Path(__file__).resolve().parent
HUB_ID = re.compile(rb"[a-zA-Z0-9_-]{8,64}")


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
        root = project / ".ryugi" / "artifacts"
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


def create_hub(project_dirs, server, namespace=None):
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
            return [body]

        host = f"{HOST}:{env['SERVER_PORT']}"
        origin = env.get("HTTP_ORIGIN")
        if env.get("HTTP_HOST") != host or (origin is not None and origin != f"http://{host}"):
            return respond("403 Forbidden", {"error": "仅接受本机分享页面的请求。"})

        path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
        if method == "GET" and path in assets:
            content_type, data = assets[path]
            return respond("200 OK", data, content_type)
        if path not in {"/api/artifacts", "/api/share"}:
            return respond("404 Not Found", {"error": "页面不存在。"})
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
                "200 OK", {"server": server, "projects": list(map(str, projects)), "items": items}
            )
        if method != "POST" or path != "/api/share":
            return respond("405 Method Not Allowed", {"error": "此接口不支持该请求方式。"})
        if origin != f"http://{host}":
            return respond("403 Forbidden", {"error": "请从本机分享页面发布。"})
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
    args = parser.parse_args()
    try:
        app, key = create_hub(args.project_dir or [Path.cwd()], args.server, args.namespace)
        with make_server(HOST, PORT, app, server_class=LocalServer) as http:
            print(
                f"在本机浏览器打开此链接以启用分享：http://{HOST}:{PORT}/#session={key}", flush=True
            )
            http.serve_forever()
    except (ValueError, OSError) as error:
        parser.exit(1, f"Hub share: {error}\n")
    except KeyboardInterrupt:
        pass

"""Push a single project artifact to an independent snapshot server."""

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from snapshot import LOCATOR, MAX_BYTES, MAX_FILES, is_private, valid_path


def artifact_files(directory):
    if directory.resolve(strict=True) != directory:
        raise ValueError(f"Cannot publish a symlinked directory: {directory}")
    for path in sorted(directory.iterdir()):
        if is_private(path.name):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            yield from artifact_files(path)
        elif stat.S_ISREG(info.st_mode):
            yield path, info
        else:
            raise ValueError(f"Only regular files can be shared: {path}")


def write_snapshot(folder, archive):
    count = size = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for count, (path, info) in enumerate(artifact_files(folder), start=1):
            name = path.relative_to(folder).as_posix()
            if not valid_path(name):
                raise ValueError(f"Unsupported artifact path: {name!r}")
            size += info.st_size
            if count > MAX_FILES or size > MAX_BYTES:
                raise ValueError("Snapshot exceeds 1,024 files or 64 MiB")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags | getattr(os, "O_BINARY", 0)), "rb") as source:
                if path.resolve(strict=True) != path or not os.path.samestat(
                    info, os.fstat(source.fileno())
                ):
                    raise ValueError(f"File was replaced during publication: {name!r}")
                data = source.read(info.st_size + 1)
                if (
                    len(data) != info.st_size
                    or info.st_mtime_ns != os.fstat(source.fileno()).st_mtime_ns
                ):
                    raise ValueError(f"File changed during publication: {name!r}")
            bundle.writestr(name, data)
    if not count:
        raise ValueError("The artifact has no files to publish")
    if archive.tell() > MAX_BYTES:
        raise ValueError("ZIP upload exceeds 64 MiB")


def destination_url(server):
    url = urlsplit(server or "")
    loopback = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not (url.scheme == "https" or (url.scheme == "http" and loopback))
    ):
        raise ValueError("Set --server to an HTTPS base URL (HTTP is available on loopback)")
    return url


def publishing_token():
    token = os.environ.get("RYUGI_SELF_HOST_TOKEN")
    if not token:
        raise ValueError("Set RYUGI_SELF_HOST_TOKEN to the server's publishing token")
    if not token.isprintable() or any(ord(char) > 255 for char in token):
        raise ValueError("RYUGI_SELF_HOST_TOKEN contains characters unsupported in an HTTP header")
    return token


def publish(project_dir, artifact, server, namespace=None):
    if not LOCATOR.fullmatch(artifact):
        raise ValueError("Expected an artifact locator such as html/demo or document/spec")
    url = destination_url(server)
    token = publishing_token()
    project = project_dir.resolve(strict=True)
    folder = project / ".ryugi" / "artifacts" / artifact
    if folder.resolve(strict=True) != folder or not folder.is_dir():
        raise ValueError("The artifact must be a directory inside the project, without symlinks")
    namespace_hash = hashlib.sha256((namespace or str(project)).encode()).hexdigest()
    prefix = url.path.rstrip("/")
    target = f"{prefix}/api/artifacts/{namespace_hash}/{artifact}"
    connection = (
        http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    )
    with tempfile.TemporaryFile() as archive:
        write_snapshot(folder, archive)
        length = archive.tell()
        archive.seek(0)
        client = connection(url.hostname, url.port, timeout=120)
        try:
            client.request(
                "PUT",
                target,
                body=archive,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/zip",
                    "Content-Length": str(length),
                },
            )
            response = client.getresponse()
            if response.status != 200:
                raise ValueError(f"Server returned HTTP {response.status}")
            result = json.loads(response.read(4096))
            path = result.get("path") if isinstance(result, dict) else None
            if not isinstance(path, str) or not re.fullmatch(r"/a/[a-f0-9]{32}/", path):
                raise ValueError("Server returned an invalid share path")
            return f"{url.scheme}://{url.netloc}{prefix}{path}"
        finally:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="type/slug inside .ryugi/artifacts")
    parser.add_argument(
        "--project-dir", type=Path, default=Path.cwd(), help="project root (default: cwd)"
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("RYUGI_SELF_HOST_URL"),
        help="base URL of the snapshot server",
    )
    parser.add_argument(
        "--namespace", help="stable project name for pushes from multiple checkouts"
    )
    args = parser.parse_args()
    try:
        print(publish(args.project_dir, args.artifact, args.server, args.namespace))
    except (OSError, ValueError, http.client.HTTPException) as error:
        parser.exit(1, f"Push failed: {error}\n")

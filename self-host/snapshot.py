"""The file and size limits shared by the snapshot publisher and receiver."""

import re

ARTIFACT_TYPES = ("artboard", "document", "html", "report", "slides", "storyboard", "verify", "whiteboard")
LOCATOR = re.compile(rf"(?:{'|'.join(ARTIFACT_TYPES)})/[a-z0-9][a-z0-9-]*")
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 1024


def is_private(part):
    part = part.casefold()
    return (
        part.startswith(".")
        or part.endswith(".loro")
        or part in {"comments.json", "comment-cursors.json", "node_modules", "server-info"}
    )


def valid_path(name):
    return (
        0 < len(name.encode("utf-8")) <= 1024
        and "\\" not in name
        and not re.match(r"^[A-Za-z]:", name)
        and not any(ord(c) < 32 or ord(c) == 127 for c in name)
        and all(part and not is_private(part) for part in name.split("/"))
    )

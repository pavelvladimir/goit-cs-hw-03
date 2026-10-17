"""Construct deliberately invalid backup bundles for recovery tests."""

import json
import zipfile
from pathlib import Path


def rewrite_bundle(source: Path, destination: Path, change: str) -> None:
    with (
        zipfile.ZipFile(source) as original,
        zipfile.ZipFile(destination, "w") as changed,
    ):
        manifest = json.loads(original.read("manifest.json"))
        payload = original.read("archive")
        if change == "checksum":
            payload = bytes([payload[0] ^ 1]) + payload[1:]
        elif change == "size":
            payload += b"corruption"
        elif change == "database":
            manifest["database"] = "another_database"
        elif change == "version":
            manifest["server_version"] = "99.0"
        elif change == "engine":
            manifest["engine"] = "MONGO"
        elif change == "format":
            manifest["format"] = 100
        elif change == "invalid":
            manifest["bytes"] = "invalid"
        changed.writestr("manifest.json", json.dumps(manifest))
        changed.writestr("archive", payload)
        if change == "entries":
            changed.writestr("../escape", "invalid")

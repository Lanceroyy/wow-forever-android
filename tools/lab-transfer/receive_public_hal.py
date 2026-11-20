#!/usr/bin/env python3
"""Verify the one reviewed public HAL archive into a fresh readonly lab directory.

Usage: python receive_public_hal.py ARCHIVE OUTPUT_DIRECTORY
This never installs a driver, contacts a network, or runs Docker or Android.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile

ARCHIVE_SHA256 = "dc9352ca778f7fce4510fd51f1dcadd9eed6573fb3bb4fd9fd3506fdafbb943a"
ARCHIVE_BYTES = 52218250
MANIFEST_NAME = "candidate-manifest.json"
MANIFEST_SHA256 = "38aac2fff4554b32ec524eac18c45edd91f1d7781b424ed07c1fe7d3f2f9672c"
MANIFEST_BYTES = 2145
IMAGE = "redroid/redroid@sha256:b21c57533662275928832602f7c09b83cb198aa9b271135ae49d978857d6795f"
NAMES = (
    "vulkan.pastel.so", "libdrm.so", "libz.so.1", "libLLVM.so", "libc++_lvpqa_.so",
    "libxml2.so.16", "libiconv.so", "libzstd.so.1", "libffi.so",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def destination(name):
    return ("/vendor/lib64/hw/" if name == "vulkan.pastel.so" else "/vendor/lib64/") + name


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def snapshot(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def verified_payloads(archive):
    require(not archive.is_symlink(), "Archive must be a regular local file")
    with archive.open("rb") as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size == ARCHIVE_BYTES, "Archive size or type differs")
        require(digest(source) == ARCHIVE_SHA256, "Archive checksum differs")
        source.seek(0)
        with tarfile.open(fileobj=source, mode="r:gz") as bundle:
            members = bundle.getmembers()
            allowed = set(NAMES) | {MANIFEST_NAME}
            require(len(members) == 10 and {item.name for item in members} == allowed, "Archive member list differs")
            require(all(item.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not item.issym()
                        and not item.islnk() for item in members), "Archive contains a non-regular member")
            by_name = {item.name: item for item in members}
            require(by_name[MANIFEST_NAME].size == MANIFEST_BYTES, "Manifest size differs")
            with bundle.extractfile(by_name[MANIFEST_NAME]) as stream:
                raw_manifest = stream.read(MANIFEST_BYTES + 1)
            require(len(raw_manifest) == MANIFEST_BYTES and
                    hashlib.sha256(raw_manifest).hexdigest() == MANIFEST_SHA256, "Manifest checksum differs")
            manifest = json.loads(raw_manifest)
            require(manifest["image"] == IMAGE and manifest["schema"] == 1, "Manifest platform differs")
            entries = manifest["files"]
            require(len(entries) == 9 and {item["name"] for item in entries} == set(NAMES), "ELF manifest list differs")
            pins = {item["name"]: item for item in entries}
            payloads = {MANIFEST_NAME: raw_manifest}
            for name in NAMES:
                item = pins[name]
                require(type(item["bytes"]) is int and 20 <= item["bytes"] < 200000000, "ELF size is invalid")
                require(isinstance(item["sha256"], str) and re.fullmatch("[0-9a-f]{64}", item["sha256"]), "ELF pin is invalid")
                require(item["destination"] == destination(name), "ELF destination differs")
                require(by_name[name].size == item["bytes"], "ELF member size differs")
                with bundle.extractfile(by_name[name]) as stream:
                    data = stream.read(item["bytes"] + 1)
                require(len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"], "ELF checksum differs")
                require(data[:6] == b"\x7fELF\x02\x01" and int.from_bytes(data[18:20], "little") == 183, "ELF is not little-endian ARM64")
                payloads[name] = data
        require(snapshot(before) == snapshot(os.fstat(source.fileno())), "Archive changed while being read")
    return payloads, pins


def receive(archive, requested):
    # Validate all bounded public payloads before creating any output. The known
    # nine ELF files total 154,082,664 bytes; this holds one fixed lab packet in RAM.
    payloads, pins = verified_payloads(archive)
    require(requested.name not in ("", ".", ".."), "Output directory name is invalid")
    output = requested.parent.resolve(strict=True) / requested.name
    output.mkdir(mode=0o700, exist_ok=False)
    identity = output.lstat()
    directory_fd = None
    if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
        directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for name in NAMES + (MANIFEST_NAME,):
            current = output.lstat()
            require(stat.S_ISDIR(current.st_mode) and
                    (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), "Output directory identity changed")
            path = output / name
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = (os.open(name, flags, 0o600, dir_fd=directory_fd) if directory_fd is not None
                          else os.open(path, flags, 0o600))
            with os.fdopen(descriptor, "w+b") as stream:
                require(stream.write(payloads[name]) == len(payloads[name]), "Output write was incomplete")
                stream.flush()
                stream.seek(0)
                expected = MANIFEST_SHA256 if name == MANIFEST_NAME else pins[name]["sha256"]
                require(digest(stream) == expected, "Written output checksum differs")
                written = os.fstat(stream.fileno())
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), 0o444)
            if not hasattr(os, "fchmod"):
                path.chmod(0o444)
            checked = path.lstat()
            require(stat.S_ISREG(checked.st_mode) and
                    (checked.st_dev, checked.st_ino, checked.st_size) ==
                    (written.st_dev, written.st_ino, len(payloads[name])) and
                    checked.st_mode & 0o222 == 0, "Output identity or readonly mode differs")
        require({item.name for item in output.iterdir()} == set(NAMES) | {MANIFEST_NAME}, "Output directory contents differ")
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return {
        "scope": "Verified public lab HAL files only; no install, Docker, guest, upload, or property changes",
        "archive_sha256": ARCHIVE_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "output_directory": str(output),
        "readonly_bind_targets": [
            {"source": str(output / name), "destination": destination(name), "readonly": True,
             "bytes": pins[name]["bytes"], "sha256": pins[name]["sha256"]}
            for name in NAMES
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = receive(args.archive, args.output)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as failure:
        print(json.dumps({"verified": False, "error_class": type(failure).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

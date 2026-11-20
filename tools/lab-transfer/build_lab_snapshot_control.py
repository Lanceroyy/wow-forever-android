"""Rebuild the reviewed lab-only Android task snapshot control from source."""

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from urllib.request import urlopen
from zipfile import ZipFile


SDK_REPOSITORY = "https://dl.google.com/android/repository/"
SDK_INPUTS = (
    ("build-tools_r35_linux.zip", 61958799, "2cfaa0bbb2336e9ec18ed3ecea84fa2e2af607bc", "lib/d8.jar",
     "305622ad00535684534eb8f742cbf5e628a9abc09d8ea4d39d1babb95bf0cee5"),
    ("platform-36_r02.zip", 65878410, "2c1a80dd4d9f7d0e6dd336ec603d9b5c55a6f576", "android.jar",
     "d9eb9da824d9e247a352f570f01e1169e725b2954bca9e283a71786c59b59f9a"),
)
SOURCE_SHA = "2fb62a1db00a8279dcdf52e782213217bbc728b696dc6cc85a3b3b59fb62d202"
DEX_SHA = "ec3f9f0a2e0ea508c88167235f5347c8c5177836c4261497db9eadeeab22df6e"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sdk_jar(work, archive, size, archive_sha1, member, jar_sha256):
    with urlopen(SDK_REPOSITORY + archive, timeout=45) as response:
        data = response.read(size + 1)
    require(len(data) == size and hashlib.sha1(data).hexdigest() == archive_sha1, "Android SDK archive differs")
    with ZipFile(BytesIO(data)) as bundle:
        matches = [item for item in bundle.infolist() if item.filename.endswith("/" + member) and not item.is_dir()]
        require(len(matches) == 1 and matches[0].file_size <= 96 * 1024 * 1024, "Android SDK JAR is missing or oversized")
        jar = bundle.read(matches[0])
    require(hashlib.sha256(jar).hexdigest() == jar_sha256, "Android SDK JAR differs from the reviewed compiler input")
    path = work / Path(member).name
    path.write_bytes(jar)
    return path


def build(output):
    src = Path(__file__).with_name("LabSnapshotControl.java")
    require(hashlib.sha256(src.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == SOURCE_SHA,
            "Snapshot control source differs")
    output.mkdir(mode=0o700)
    d8, android = [sdk_jar(output, *entry) for entry in SDK_INPUTS]
    classes = output / "classes"
    classes.mkdir()
    java_home = os.environ.get("JAVA_HOME_17_ARM64") or os.environ.get("JAVA_HOME")
    suffix = ".exe" if os.name == "nt" else ""
    javac = str(Path(java_home) / "bin" / ("javac" + suffix)) if java_home else "javac"
    java = str(Path(java_home) / "bin" / ("java" + suffix)) if java_home else "java"
    subprocess.run([javac, "--release", "8", "-g:none", "-d", str(classes), str(src)], check=True, timeout=60)
    subprocess.run([java, "-cp", str(d8), "com.android.tools.r8.D8", "--release", "--min-api", "34",
                    "--lib", str(android), "--output", str(output), str(classes / "LabSnapshotControl.class")],
                   check=True, timeout=60)
    dex = (output / "classes.dex").read_bytes()
    require(len(dex) == 2444 and hashlib.sha256(dex).hexdigest() == DEX_SHA, "Snapshot control is not reproducible")
    print(json.dumps({"scope": "Source-built Android lab control", "dex_sha256": DEX_SHA, "dex_bytes": len(dex)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New temporary build directory")
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()

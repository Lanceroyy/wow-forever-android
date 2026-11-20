#!/usr/bin/env python3
"""Read the authorized GitHub draft with a job token and verify all input bytes."""

import argparse
import hashlib
from http.client import HTTPException
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPOSITORY = "Lanceroyy/wow-forever-android"
REPOSITORY_ID = 1451984864
OWNER_ID = 93947654
BRANCH = "security/hardening-v1"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
MANIFEST = "native-arm64-lab-inputs.json"
MANIFEST_SIZE = 1705
MANIFEST_SHA = "f18f1926ad258f0fe5faab3f807e67913bb8508c37a24a3f54db1627ef7bc3f0"
PAYLOADS = {
    "WoW-Handheld-S25plus-prelogin-1b8e7a3be0dc.apk": (
        "main.apk", 129571767, "1b8e7a3be0dc490b727ec22a7d548dd1c65423ca57e7e6abe18695b10202f62e"),
    "wow-handheld-gameframe-qa-fce7f62f1125.apk": (
        "qa.apk", 7403478, "859b6cc24945988fb8a55d32fc5152fee8b52dddc91d52f987b261f1a6003ac0"),
    "candidate-public-build.tar.gz": (
        "candidate-public-build.tar.gz", 52218250, "dc9352ca778f7fce4510fd51f1dcadd9eed6573fb3bb4fd9fd3506fdafbb943a"),
}
PINS = {name: values[1:] for name, values in PAYLOADS.items()}
PINS[MANIFEST] = (MANIFEST_SIZE, MANIFEST_SHA)
DOWNLOAD_HOSTS = ("release-assets.githubusercontent.com", "objects.githubusercontent.com")


class InputError(RuntimeError):
    def __init__(self, code, status=None):
        super().__init__(code)
        self.status = status


def require(condition, code):
    if not condition:
        raise InputError(code)


class DoNotRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DraftReader:
    def __init__(self, token):
        require(bool(token), "missing_job_token")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), DoNotRedirect())
        self.deadline = time.monotonic() + 390

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "download_deadline_exceeded")
        return remaining

    def request(self, url, accept, authenticated, *, revoke=False):
        target = urlsplit(url)
        hosts = ("api.github.com",) if authenticated else DOWNLOAD_HOSTS
        require(target.scheme == "https" and target.hostname in hosts and target.port in (None, 443)
                and target.username is None and target.password is None and not target.fragment,
                "unexpected_download_destination")
        headers = {"Accept": accept, "User-Agent": "wow-handheld-native-lab"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        if revoke:
            require(authenticated and url == "https://api.github.com/installation/token", "unexpected_revocation_destination")
        try:
            return self.opener.open(Request(url, headers=headers, method="DELETE" if revoke else "GET"),
                                    timeout=15 if revoke else min(self.remaining(), 35))
        except HTTPError as failure:
            return failure

    def revoke_job_token(self):
        with self.request("https://api.github.com/installation/token", "application/vnd.github+json",
                          True, revoke=True) as response:
            if response.status != 204:
                raise InputError("job_token_revocation_unconfirmed", response.status)

    def release(self, release_id):
        with self.request(f"{API}/{release_id}", "application/vnd.github+json", True) as response:
            if response.status != 200:
                raise InputError("draft_metadata_unavailable", response.status)
            body = response.read(65537)
            require(len(body) <= 65536, "release_metadata_too_large")
            release = json.loads(body)
        require(release["id"] == release_id and release["draft"] is True
                and release["published_at"] is None and release["prerelease"] is True
                and release["target_commitish"] == BRANCH and release["author"]["id"] == OWNER_ID,
                "release_is_not_the_authorized_draft")
        assets = release["assets"]
        require(len(assets) == len(PINS) and {asset["name"] for asset in assets} == set(PINS),
                "unexpected_draft_asset_set")
        require(len({asset["id"] for asset in assets}) == len(PINS), "duplicate_draft_asset_ids")
        pins = {}
        for asset in assets:
            size, sha = PINS[asset["name"]]
            require(type(asset["id"]) is int and asset["id"] > 0 and asset["size"] == size
                    and asset["digest"] == f"sha256:{sha}" and asset["state"] == "uploaded"
                    and asset["uploader"]["id"] == OWNER_ID, "draft_asset_metadata_differs")
            pins[asset["name"]] = asset["id"]
        return release["tag_name"], pins

    def asset(self, asset_id, name, output):
        size, sha = PINS[name]
        url = f"{API}/assets/{asset_id}"
        authenticated = True
        for _ in range(3):
            with self.request(url, "application/octet-stream", authenticated) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = response.headers.get("Location", "")
                    authenticated = False
                    continue
                if response.status != 200:
                    raise InputError("draft_asset_download_unavailable", response.status)
                digest = hashlib.sha256()
                total = 0
                while total < size:
                    self.remaining()
                    chunk = response.read(min(size - total, 1024 * 1024))
                    require(bool(chunk), "draft_asset_truncated")
                    digest.update(chunk)
                    output.write(chunk)
                    total += len(chunk)
                require(not response.read(1), "draft_asset_size_differs")
                require(digest.hexdigest() == sha, "draft_asset_checksum_differs")
                return
        raise InputError("too_many_download_redirects")


def get_inputs(reader, release_id, destination):
    tag, assets = reader.release(release_id)
    with BytesIO() as output:
        reader.asset(assets[MANIFEST], MANIFEST, output)
        manifest = json.loads(output.getvalue())
    require(manifest["repository"] == REPOSITORY and manifest["branch"] == BRANCH
            and manifest["release_tag"] == tag and len(manifest["artifacts"]) == len(PAYLOADS)
            and {row["name"]: (row["bytes"], row["sha256"]) for row in manifest["artifacts"]}
            == {name: values[1:] for name, values in PAYLOADS.items()}, "draft_manifest_pins_differ")
    if destination is None:
        return
    destination.mkdir(mode=0o700)
    for name, (filename, _, _) in PAYLOADS.items():
        with (destination / filename).open("xb") as output:
            reader.asset(assets[name], name, output)
    require(reader.release(release_id) == (tag, assets), "draft_changed_during_download")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Fresh private directory. Omit for a manifest-only access check.")
    args = parser.parse_args()
    report = {"scope": "draft_manifest_access" if args.output is None else "verified_draft_download", "passed": False}
    reader = None
    try:
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and
                os.environ.get("GITHUB_REPOSITORY_ID") == str(REPOSITORY_ID) and
                os.environ.get("GITHUB_REPOSITORY_OWNER_ID") == str(OWNER_ID) and
                os.environ.get("GITHUB_REF") == f"refs/heads/{BRANCH}", "unexpected_workflow_repository_or_ref")
        require(os.environ.get("GITHUB_ACTIONS") == "true" and
                os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
                os.environ.get("GITHUB_ACTOR_ID") == str(OWNER_ID) and
                os.environ.get("GITHUB_WORKFLOW_REF") ==
                f"{REPOSITORY}/.github/workflows/arm64-apk-lab.yml@refs/heads/{BRANCH}", "unexpected_workflow_invocation")
        value = os.environ.get("LAB_RELEASE_ID", "")
        require(re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None, "invalid_numeric_release_id")
        reader = DraftReader(os.environ.pop("LAB_JOB_TOKEN", ""))
        get_inputs(reader, int(value), args.output)
        report.update(passed=True, manifest_verified=True, payloads_verified=0 if args.output is None else 3)
    except InputError as failure:
        report["failure_class"] = str(failure)
        if failure.status is not None:
            report["http_status"] = failure.status
    except (OSError, HTTPException, KeyError, TypeError, ValueError) as failure:
        report["failure_class"] = type(failure).__name__
    finally:
        if reader is not None:
            try:
                reader.revoke_job_token()
                report["job_token_revoked"] = True
            except (InputError, OSError, HTTPException) as failure:
                report.update(passed=False, job_token_revoked=False, revocation_failed=True)
                if isinstance(failure, InputError) and failure.status is not None:
                    report["revocation_http_status"] = failure.status
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

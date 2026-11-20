"""Check the draft input boundary without credentials or network access."""

import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from download_lab_inputs import API, DraftReader, InputError, OWNER_ID, PINS


class Response(BytesIO):
    def __init__(self, status, body=b"", headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class DraftDownloadTests(unittest.TestCase):
    def setUp(self):
        self.reader = DraftReader("placeholder-for-test")

    def test_storage_redirect_receives_no_job_token(self):
        data = b"reviewed bytes"
        redirect = "https://release-assets.githubusercontent.com/example"
        with patch.dict(PINS, {"test.bin": (len(data), hashlib.sha256(data).hexdigest())}), \
             patch.object(self.reader.opener, "open", side_effect=[
                 Response(302, headers={"Location": redirect}), Response(200, data),
             ]) as send:
            output = BytesIO()
            self.reader.asset(123, "test.bin", output)
        self.assertEqual(output.getvalue(), data)
        api_request, storage_request = [call.args[0] for call in send.call_args_list]
        self.assertEqual(api_request.full_url, f"{API}/assets/123")
        self.assertEqual(api_request.get_header("Authorization"), "Bearer placeholder-for-test")
        self.assertEqual(storage_request.full_url, redirect)
        self.assertIsNone(storage_request.get_header("Authorization"))

    def test_unknown_storage_host_is_never_contacted(self):
        with patch.object(self.reader.opener, "open", return_value=Response(
            302, headers={"Location": "https://github.com.example.invalid/file"},
        )) as send, self.assertRaisesRegex(InputError, "unexpected_download_destination"):
            self.reader.asset(123, "native-arm64-lab-inputs.json", BytesIO())
        self.assertEqual(send.call_count, 1)

    def test_size_or_digest_mismatch_fails(self):
        wanted = b"wanted"
        with patch.dict(PINS, {"test.bin": (len(wanted), hashlib.sha256(wanted).hexdigest())}):
            for actual, code in ((b"want", "truncated"), (b"wanted!", "size_differs"),
                                 (b"tamper", "checksum_differs")):
                with self.subTest(code=code), patch.object(self.reader.opener, "open",
                                                           return_value=Response(200, actual)), \
                     self.assertRaisesRegex(InputError, f"draft_asset_{code}"):
                    self.reader.asset(123, "test.bin", BytesIO())

    def test_metadata_access_error_reports_only_fixed_fields(self):
        with patch.object(self.reader.opener, "open", return_value=Response(403, b"private response")), \
             self.assertRaisesRegex(InputError, "draft_metadata_unavailable") as failure:
            self.reader.release(123)
        self.assertEqual(failure.exception.status, 403)

    def test_published_release_is_rejected(self):
        release = {"id": 123, "draft": False, "published_at": "2026-11-20T00:00:00Z",
                   "prerelease": True, "author": {"id": OWNER_ID}}
        with patch.object(self.reader.opener, "open", return_value=Response(200, json.dumps(release).encode())), \
             self.assertRaisesRegex(InputError, "release_is_not_the_authorized_draft"):
            self.reader.release(123)

    def test_revocation_uses_only_the_job_token_endpoint(self):
        with patch.object(self.reader.opener, "open", return_value=Response(204)) as send:
            self.reader.revoke_job_token()
        revoke = send.call_args.args[0]
        self.assertEqual(revoke.full_url, "https://api.github.com/installation/token")
        self.assertEqual(revoke.get_method(), "DELETE")
        self.assertEqual(revoke.get_header("Authorization"), "Bearer placeholder-for-test")

    def test_revocation_redirect_is_rejected(self):
        with patch.object(self.reader.opener, "open", return_value=Response(302)) as send, \
             self.assertRaisesRegex(InputError, "job_token_revocation_unconfirmed"):
            self.reader.revoke_job_token()
        self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()

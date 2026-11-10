"""Unit tests for the BYO CI/CD commands: `entitlement resolve` and `fetch-bundle`.

No network / AWS: the StrataHQ client, the AWS session helpers, and the artifact
download/verify helpers are all mocked. These assert the behaviour the spec requires —
reuse of the existing entitlement + verification logic, fail-closed signature handling,
secret hygiene (no URL/token in logs, 0600 files), version resolution, and that no
CodeBuild / StrataCI launch happens on either path.

Run: python -m unittest discover -s tests  (from the stratactl package root)
"""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from strata.commands import entitlement, fetch_bundle
from strata.errors import ArtifactError, EntitlementDenied, ConfigError, StrataError
from strata.hq.client import EntitlementResult

URL = "https://s3.example.com/stratabi/1.7.1/module.zip?X-Amz-Signature=deadbeef&token=abc"
SIG = "MEUCIQ-base64-signature-value"
SHA = "a" * 64


def _settings(public_key=None, require_signature=False, region="us-east-1"):
    return SimpleNamespace(
        region=region,
        hq=SimpleNamespace(region=None, public_key=public_key, require_signature=require_signature),
    )


def _ent(version="1.7.1", artifact_url=URL, sha256=SHA, signature=SIG, expires_at="2026-11-10T22:00:00+00:00"):
    return EntitlementResult(allow=True, module_id="stratabi", version=version,
                             artifact_url=artifact_url, sha256=sha256, signature=signature,
                             expires_at=expires_at)


class _Base(unittest.TestCase):
    def setUp(self):
        # Patch the session helpers so no AWS is touched; account is "verified".
        self._p = [
            mock.patch.object(entitlement.aws_session, "make_session", return_value=object()),
            mock.patch.object(entitlement.aws_session, "account_id", return_value="063876841308"),
            # Guard: if any code path tried to launch the CodeBuild runner, this would fire.
            mock.patch("strata.runner.launcher.for_settings", side_effect=AssertionError("CodeBuild launched!")),
        ]
        for p in self._p:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._p])


class TestEntitlementResolve(_Base):
    def test_valid_writes_metadata_and_resolves_version(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "sub" / "entitlement.json"
            args = SimpleNamespace(ent_action="resolve", module="stratabi", version="latest", output=str(out))
            buf = io.StringIO()
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()) as ce, \
                 redirect_stdout(buf):
                rc = entitlement.run(_settings(), args)
            self.assertEqual(rc, 0)
            # requested "latest" -> the SERVER-resolved version is what we persist
            self.assertEqual(ce.call_args.kwargs["version"], "latest")
            data = json.loads(out.read_text())
            self.assertEqual(data["version"], "1.7.1")
            self.assertEqual(data["artifact_url"], URL)
            self.assertEqual(data["sha256"], SHA)
            self.assertEqual(data["signature"], SIG)
            # secret hygiene: the URL/signature are in the FILE but never in stdout
            self.assertNotIn(URL, buf.getvalue())
            self.assertNotIn(SIG, buf.getvalue())

    def test_output_file_is_0600(self):
        if os.name != "posix":
            self.skipTest("POSIX modes only")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "entitlement.json"
            args = SimpleNamespace(ent_action="resolve", module="stratabi", version="latest", output=str(out))
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 redirect_stdout(io.StringIO()):
                entitlement.run(_settings(), args)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def test_requires_output(self):
        args = SimpleNamespace(ent_action="resolve", module="stratabi", version="latest", output=None)
        with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()):
            with self.assertRaises(ConfigError):
                entitlement.run(_settings(), args)

    def test_invalid_token_denied(self):
        with tempfile.TemporaryDirectory() as d:
            args = SimpleNamespace(ent_action="resolve", module="stratabi", version="latest",
                                   output=str(Path(d) / "e.json"))
            with mock.patch.object(entitlement.hq, "check_entitlement",
                                   side_effect=EntitlementDenied("denied")):
                with self.assertRaises(EntitlementDenied):
                    entitlement.run(_settings(), args)

    def test_account_mismatch_denied(self):
        # HQ returns 401/403 when the SigV4 account != the licensed account.
        with tempfile.TemporaryDirectory() as d:
            args = SimpleNamespace(ent_action="resolve", module="stratabi", version="latest",
                                   output=str(Path(d) / "e.json"))
            with mock.patch.object(entitlement.hq, "check_entitlement",
                                   side_effect=EntitlementDenied("StrataHQ denied (HTTP 403)")):
                with self.assertRaises(EntitlementDenied):
                    entitlement.run(_settings(), args)


class TestFetchBundle(_Base):
    def _download_creates(self, path_bytes=b"zip"):
        def _dl(url, dest, *a, **k):
            Path(dest).write_bytes(path_bytes)
            return dest
        return _dl

    def test_happy_path_signature_verified(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "artifacts" / "module.zip"
            meta = Path(d) / "receipt.json"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=str(meta))
            buf = io.StringIO()
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 mock.patch.object(fetch_bundle.bundle, "download_url", side_effect=self._download_creates()), \
                 mock.patch.object(fetch_bundle.bundle, "verify_sha256", return_value=SHA) as vs, \
                 mock.patch.object(fetch_bundle.bundle, "verify_signature") as vsig, \
                 redirect_stdout(buf):
                rc = fetch_bundle.run(_settings(public_key="PINNEDKEY"), args)
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            vs.assert_called_once()
            vsig.assert_called_once_with(SHA, SIG, "PINNEDKEY")
            # receipt excludes the presigned URL
            receipt = json.loads(meta.read_text())
            self.assertNotIn("artifact_url", receipt)
            self.assertEqual(receipt["version"], "1.7.1")
            self.assertNotIn(URL, buf.getvalue())

    def test_bad_sha256_fails(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "module.zip"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=None)
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 mock.patch.object(fetch_bundle.bundle, "download_url", side_effect=self._download_creates()), \
                 mock.patch.object(fetch_bundle.bundle, "verify_sha256",
                                   side_effect=ArtifactError("sha256 mismatch")):
                with self.assertRaises(ArtifactError):
                    fetch_bundle.run(_settings(public_key="PINNEDKEY"), args)

    def test_require_signature_but_unavailable_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "module.zip"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=None)
            # require_signature on, but no pinned public key -> refuse + delete partial
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 mock.patch.object(fetch_bundle.bundle, "download_url", side_effect=self._download_creates()), \
                 mock.patch.object(fetch_bundle.bundle, "verify_sha256", return_value=SHA):
                with self.assertRaises(StrataError):
                    fetch_bundle.run(_settings(public_key=None, require_signature=True), args)
            self.assertFalse(out.exists())  # partial file removed

    def test_signature_present_no_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "module.zip"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=None)
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 mock.patch.object(fetch_bundle.bundle, "download_url", side_effect=self._download_creates()), \
                 mock.patch.object(fetch_bundle.bundle, "verify_sha256", return_value=SHA):
                with self.assertRaises(StrataError):
                    fetch_bundle.run(_settings(public_key=None, require_signature=False), args)
            self.assertFalse(out.exists())

    def test_hash_only_allowed_when_no_signature(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "module.zip"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=None)
            ent = _ent(signature=None)  # server returned no signature
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=ent), \
                 mock.patch.object(fetch_bundle.bundle, "download_url", side_effect=self._download_creates()), \
                 mock.patch.object(fetch_bundle.bundle, "verify_sha256", return_value=SHA), \
                 redirect_stdout(io.StringIO()):
                rc = fetch_bundle.run(_settings(public_key=None, require_signature=False), args)
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())

    def test_expired_url_download_fails(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "module.zip"
            args = SimpleNamespace(module="stratabi", version="1.7.1", output=str(out), metadata=None)
            with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()), \
                 mock.patch.object(fetch_bundle.bundle, "download_url",
                                   side_effect=ArtifactError("403 expired presigned URL")):
                with self.assertRaises(ArtifactError):
                    fetch_bundle.run(_settings(public_key="PINNEDKEY"), args)

    def test_requires_output(self):
        args = SimpleNamespace(module="stratabi", version="1.7.1", output=None, metadata=None)
        with mock.patch.object(entitlement.hq, "check_entitlement", return_value=_ent()):
            with self.assertRaises(ConfigError):
                fetch_bundle.run(_settings(), args)


class TestCliWiring(unittest.TestCase):
    def test_entitlement_resolve_parses_and_requires_output(self):
        from strata.cli import _build_parser
        p = _build_parser()
        ns = p.parse_args(["entitlement", "resolve", "--module", "stratabi", "--output", "/tmp/e.json"])
        self.assertEqual(ns.command, "entitlement")
        self.assertEqual(ns.ent_action, "resolve")
        self.assertEqual(ns.output, "/tmp/e.json")
        with self.assertRaises(SystemExit):  # --output is required
            p.parse_args(["entitlement", "resolve", "--module", "stratabi"])

    def test_fetch_bundle_parses(self):
        from strata.cli import _build_parser
        p = _build_parser()
        ns = p.parse_args(["fetch-bundle", "--output", "/tmp/module.zip"])
        self.assertEqual(ns.command, "fetch-bundle")
        self.assertEqual(ns.module, "stratabi")   # default
        self.assertEqual(ns.version, "latest")    # default


if __name__ == "__main__":
    unittest.main()

"""Artifact bundle handling, used by the runner: download, verify, safe-extract.

Bundle layout:
    module.zip
      ├── stratabi-module.json
      ├── tofu/{main,variables,outputs,versions}.tf
      ├── config/defaults.json
      └── README.md
"""

from __future__ import annotations

import hashlib
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from strata.errors import ArtifactError

MAX_BUNDLE_BYTES = 256 * 1024 * 1024   # 256 MB cap
MAX_FILES = 5000


def download_url(url: str, dest: Path, timeout: int = 60) -> Path:
    """Download a presigned (or any) URL to dest. Never logs the URL."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as fh:
            total = 0
            while chunk := resp.read(1024 * 256):
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:
                    raise ArtifactError("bundle exceeds size cap")
                fh.write(chunk)
    except OSError as exc:
        raise ArtifactError(f"failed to download artifact: {exc}") from exc
    return dest


def download_s3(s3_client, s3_uri: str, dest: Path) -> Path:
    if not s3_uri.startswith("s3://"):
        raise ArtifactError(f"not an s3 uri: {s3_uri}")
    bucket, key = s3_uri[len("s3://"):].split("/", 1)
    try:
        s3_client.download_file(bucket, key, str(dest))
    except Exception as exc:
        raise ArtifactError(f"failed to download {s3_uri}: {exc}") from exc
    return dest


def verify_sha256(path: Path, expected: Optional[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 256):
            h.update(chunk)
    actual = h.hexdigest()
    if expected and actual.lower() != expected.lower():
        raise ArtifactError(
            f"artifact checksum mismatch (expected {expected[:12]}…, got {actual[:12]}…)"
        )
    return actual


def safe_extract(zip_path: Path, workdir: Path) -> Path:
    """Extract a zip, rejecting absolute paths / traversal / overlarge bundles."""
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.namelist()
            if len(members) > MAX_FILES:
                raise ArtifactError("bundle has too many entries")
            for name in members:
                target = (workdir / name).resolve()
                if not str(target).startswith(str(workdir.resolve())):
                    raise ArtifactError(f"unsafe path in bundle: {name}")
            zf.extractall(workdir)
    except zipfile.BadZipFile as exc:
        raise ArtifactError(f"corrupt bundle: {exc}") from exc
    return workdir


def verify_signature(digest_hex: str, signature_b64: str, public_key_b64der: str) -> None:
    """Offline verify a KMS ECDSA-P256 signature over the bundle's sha256 digest
    (Decision C). No AWS call, no cross-account KMS. Raises ArtifactError on
    failure. `cryptography` is imported lazily — only needed when verifying."""
    import base64

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.hazmat.primitives.serialization import load_der_public_key
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ArtifactError("signature verification requires the 'cryptography' package") from exc

    try:
        pub = load_der_public_key(base64.b64decode(public_key_b64der))
        pub.verify(
            base64.b64decode(signature_b64),
            bytes.fromhex(digest_hex),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
    except Exception as exc:  # invalid signature or key
        raise ArtifactError(f"bundle signature verification failed: {exc}") from exc


def cache_to_customer_s3(s3_client, bucket: str, module_id: str, version: str,
                         zip_path: Path) -> str:
    """Store the verified bundle so uninstall/update work without StrataHQ."""
    key = f"artifacts/{module_id}/{version}/module.zip"
    s3_client.upload_file(str(zip_path), bucket, key)
    return f"s3://{bucket}/{key}"

"""Native pytest coverage for POST /upload's input hardening.

Three things are pinned:

* the size cap is enforced WHILE reading, so an oversized body is refused
  without first being materialised as one bytes object;
* the declared extension has to match the actual leading bytes for formats that
  have a signature — previously the extension alone decided what a file was, and
  the file is later handed to Claude's Read tool as that type;
* a save failure returns a generic message. The OSError text embeds the absolute
  destination path, which on Windows contains the account name.

Text formats are deliberately exempt from the signature check: .txt/.md/.csv/
.json/.py have no magic bytes, so there is nothing to verify and a guess would
just reject valid files.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_upload_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "upload-test-token-" + "u" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

# Keep every write inside a throwaway dir — never the owner's real data tree.
_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_upload_dir_"))
config.UPLOAD_DIR = _SANDBOX
config.PERM_WRITE_DIRS = list(config.PERM_WRITE_DIRS) + [str(_SANDBOX)]

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


def _post(filename: str, body: bytes):
    return client.post(
        "/upload",
        headers=AUTH,
        files={"file": (filename, io.BytesIO(body), "application/octet-stream")},
    )


def test_requires_a_token():
    r = client.post("/upload", files={"file": ("a.png", io.BytesIO(PNG), "image/png")})
    assert r.status_code == 403


@pytest.mark.parametrize("name,body", [("a.png", PNG), ("b.jpg", JPEG), ("c.pdf", PDF)])
def test_valid_binary_uploads_are_accepted(name: str, body: bytes):
    r = _post(name, body)
    assert r.status_code == 200, r.text
    saved = Path(r.json()["path"])
    assert saved.is_file()
    assert saved.parent == _SANDBOX
    assert saved.read_bytes() == body


def test_text_types_skip_the_signature_check():
    """No magic bytes exist for these, so any content is legitimate."""
    for name in ("notes.txt", "data.csv", "x.md", "y.json"):
        r = _post(name, b"anything at all, really")
        assert r.status_code == 200, f"{name}: {r.text}"


def test_extension_content_mismatch_is_rejected():
    """A .png that is not a PNG. Previously stored and later read AS a png."""
    r = _post("evil.png", b"MZ\x90\x00this is a windows executable")
    assert r.status_code == 415
    assert "don't match" in r.json()["detail"]


def test_every_signed_format_rejects_foreign_content():
    for name in ("a.png", "b.jpg", "c.gif", "d.pdf", "e.webp", "f.bmp", "g.heic"):
        r = _post(name, b"\x01\x02\x03 not this format at all")
        assert r.status_code == 415, f"{name} accepted foreign content: {r.text}"


def test_unsupported_extension_is_rejected():
    assert _post("payload.exe", b"MZ\x90\x00").status_code == 415
    assert _post("script.sh", b"#!/bin/sh\n").status_code == 415


def test_empty_file_is_rejected():
    assert _post("empty.txt", b"").status_code == 400


def test_oversized_upload_is_refused():
    real_cap = config.UPLOAD_MAX_BYTES
    config.UPLOAD_MAX_BYTES = 1024
    try:
        r = _post("big.txt", b"x" * 4096)
        assert r.status_code == 413
    finally:
        config.UPLOAD_MAX_BYTES = real_cap


def test_filename_is_sanitised_and_cannot_traverse():
    r = _post("../../etc/pa'ss wd.txt", b"hello")
    assert r.status_code == 200, r.text
    saved = Path(r.json()["path"])
    # What matters is where the bytes landed, not whether ".." survives as text.
    # The sanitiser maps every separator to "_", so "../../etc/x" becomes the
    # literal filename ".._.._etc_x" — dots with no separators traverse nothing.
    assert saved.resolve().parent == _SANDBOX.resolve()
    assert "/" not in saved.name and "\\" not in saved.name
    assert "'" not in saved.name
    assert saved.read_bytes() == b"hello"


def test_save_failure_does_not_leak_the_path():
    """The OSError text embeds the absolute destination; the client gets a
    generic message instead."""
    real_write = Path.write_bytes
    leaky = f"Permission denied: {_SANDBOX / 'secret-account-name'}"

    def boom(self, data):
        raise OSError(leaky)

    Path.write_bytes = boom
    try:
        r = _post("x.txt", b"hi")
        assert r.status_code == 500
        assert str(_SANDBOX) not in r.text, r.text
        assert "secret-account-name" not in r.text
        assert r.json()["detail"] == "Could not save the upload"
    finally:
        Path.write_bytes = real_write

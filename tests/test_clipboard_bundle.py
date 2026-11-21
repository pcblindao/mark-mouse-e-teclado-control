import hashlib
import json
import os
import stat
import struct
import zipfile
from io import BytesIO

import pytest
from PIL import Image

from markcontrol.clipboard import ClipboardError, ClipboardPayload
from markcontrol import clipboard_bundle as b


def rewrite(path, manifest_change=None, extra=None, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path) as archive:
        content = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    manifest = json.loads(content["manifest.json"])
    if manifest_change:
        manifest_change(manifest)
    content["manifest.json"] = json.dumps(manifest).encode()
    if extra:
        content.update(extra)
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, value in content.items():
            archive.writestr(name, value)


@pytest.mark.parametrize("kind", ["text", "image"])
def test_text_and_png_roundtrip_without_any_clipboard_access(tmp_path, kind):
    if kind == "text":
        value = "Olá\n🙂" * 100
    else:
        output = BytesIO()
        Image.new("RGBA", (200, 120), (20, 50, 90, 128)).save(output, "PNG")
        value = output.getvalue()
    payload = ClipboardPayload(kind, value)
    target = tmp_path / "bundle.zip"
    b.create_bundle(payload, target, lambda: True)
    assert b.inspect_bundle_kind(target) == kind
    assert b.apply_bundle(target, tmp_path / "received", lambda: True) == payload
    assert not (tmp_path / "received").exists()


def test_files_folders_empty_files_and_repeated_paste_never_overwrite(tmp_path):
    source = tmp_path / "Origem com ação"
    (source / "subpasta").mkdir(parents=True)
    (source / "subpasta" / "arquivo.txt").write_bytes(b"data" * 400000)
    (source / "vazio.txt").touch()
    (source / "pasta vazia").mkdir()
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, lambda: True)
    assert b.inspect_bundle_kind(bundle) == "files"
    root = tmp_path / "received"
    root.mkdir()
    sentinel = root / source.name
    sentinel.write_text("original")
    first = b.apply_bundle(bundle, root, lambda: True)
    second = b.apply_bundle(bundle, root, lambda: True)
    assert first.kind == second.kind == "files" and first.value != second.value
    from pathlib import Path
    copied = Path(first.value[0])
    assert (copied / "subpasta" / "arquivo.txt").read_bytes() == (source / "subpasta" / "arquivo.txt").read_bytes()
    assert (copied / "vazio.txt").stat().st_size == 0
    assert (copied / "pasta vazia").is_dir()
    assert sentinel.read_text() == "original"


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "C:escape", "CON", "a.", "x ", "", ".."])
def test_manifest_rejects_traversal_and_incompatible_names_before_extraction(tmp_path, name):
    source = tmp_path / "file.txt"
    source.write_text("data")
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, lambda: True)
    rewrite(bundle, lambda manifest: manifest["entries"][0].update(name=name))
    root = tmp_path / "received"
    with pytest.raises(ClipboardError):
        b.inspect_bundle_kind(bundle)
    with pytest.raises(ClipboardError):
        b.apply_bundle(bundle, root, lambda: True)
    assert not root.exists()


@pytest.mark.parametrize("change", [
    lambda m: m.update(extra="bad"),
    lambda m: m["entries"][0].update(parent=0),
    lambda m: m["entries"][0].update(size=True),
    lambda m: m["entries"][0].update(sha256="invalid"),
    lambda m: m["entries"][0].pop("sha256"),
    lambda m: m["entries"][0].update(member="../file"),
])
def test_invalid_metadata_rejected_before_materializing_files(tmp_path, change):
    source = tmp_path / "file.txt"
    source.write_text("data")
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, lambda: True)
    rewrite(bundle, change)
    with pytest.raises(ClipboardError):
        b.apply_bundle(bundle, tmp_path / "received", lambda: True)
    assert not (tmp_path / "received").exists()


def test_checksum_failure_removes_only_its_transaction(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("data")
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, lambda: True)
    rewrite(bundle, lambda m: m["entries"][0].update(sha256="0" * 64))
    root = tmp_path / "received"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(ClipboardError):
        b.apply_bundle(bundle, root, lambda: True)
    assert list(root.iterdir()) == [sentinel] and sentinel.read_text() == "keep"


def test_compressed_extra_duplicate_and_link_members_rejected(tmp_path):
    bundle = tmp_path / "bundle.zip"
    payload = ClipboardPayload("text", "hello")
    for mode in ("compressed", "extra", "duplicate", "link"):
        b.create_bundle(payload, bundle, lambda: True)
        if mode == "compressed":
            rewrite(bundle, compression=zipfile.ZIP_DEFLATED)
        elif mode == "extra":
            rewrite(bundle, extra={"../escape": b"bad"})
        elif mode == "duplicate":
            with zipfile.ZipFile(bundle, "a") as archive:
                with pytest.warns(UserWarning):
                    archive.writestr("manifest.json", b"{}")
        else:
            with zipfile.ZipFile(bundle, "a") as archive:
                info = zipfile.ZipInfo("link")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "outside")
        with pytest.raises(ClipboardError):
            b.inspect_bundle_kind(bundle)


def test_cancel_during_creation_preserves_existing_destination(tmp_path):
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * (3 * b.BLOCK))
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"original destination")
    calls = [0]
    def active():
        calls[0] += 1
        return calls[0] <= 7
    with pytest.raises(ClipboardError):
        b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, active)
    assert bundle.read_bytes() == b"original destination"
    assert not list(tmp_path.glob(".clipboard-*.zip"))


def test_cancel_during_apply_removes_partial_received_files(tmp_path):
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * (3 * b.BLOCK))
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("files", (str(source),)), bundle, lambda: True)
    root = tmp_path / "received"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("keep")
    calls = [0]
    def active():
        calls[0] += 1
        return calls[0] <= 4
    with pytest.raises(ClipboardError):
        b.apply_bundle(bundle, root, active)
    assert list(root.iterdir()) == [sentinel]


def test_sources_enforce_quantity_and_total_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for number in range(4):
        (source / str(number)).write_bytes(b"x" * 400)
    monkeypatch.setattr(b, "MAX_ENTRIES", 3)
    with pytest.raises(ClipboardError):
        b.create_bundle(ClipboardPayload("files", (str(source),)), tmp_path / "bundle.zip", lambda: True)
    monkeypatch.setattr(b, "MAX_ENTRIES", 256)
    monkeypatch.setattr(b, "MAX_FILE_BYTES", 1024)
    with pytest.raises(ClipboardError):
        b.create_bundle(ClipboardPayload("files", (str(source),)), tmp_path / "bundle.zip", lambda: True)


def test_source_links_and_redirected_receive_root_are_rejected(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("data")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Creating symbolic links requires OS permission")
    with pytest.raises(ClipboardError):
        b.create_bundle(ClipboardPayload("files", (str(link),)), tmp_path / "bundle.zip", lambda: True)
    b.create_bundle(ClipboardPayload("files", (str(source),)), tmp_path / "bundle.zip", lambda: True)
    root = tmp_path / "real"
    root.mkdir()
    redirected = tmp_path / "redirected"
    redirected.symlink_to(root, target_is_directory=True)
    with pytest.raises(ClipboardError):
        b.apply_bundle(tmp_path / "bundle.zip", redirected, lambda: True)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("field,value", [(10, 65535), (12, b.MANIFEST_LIMIT + 1)])
def test_central_directory_limits_are_checked_before_zipfile_parses_entries(tmp_path, monkeypatch, field, value):
    bundle = tmp_path / "bundle.zip"
    b.create_bundle(ClipboardPayload("text", "test"), bundle, lambda: True)
    data = bytearray(bundle.read_bytes())
    end = data.rfind(b"PK\x05\x06")
    struct.pack_into("<H" if field == 10 else "<I", data, end + field, value)
    bundle.write_bytes(data)
    from unittest.mock import Mock
    opening = Mock(side_effect=AssertionError("ZipFile parser must not run"))
    monkeypatch.setattr(b.zipfile, "ZipFile", opening)
    with pytest.raises(ClipboardError):
        b.inspect_bundle_kind(bundle)
    opening.assert_not_called()

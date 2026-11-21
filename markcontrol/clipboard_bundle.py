"""Bounded, verified clipboard containers. Archive names never become file paths."""
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
from pathlib import Path

from .clipboard import (ClipboardError, ClipboardPayload, IMAGE_LIMIT, MAX_BYTES,
                        MAX_ENTRIES, check_payload, check_png, check_text, _active)

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_FILE_BYTES + 1024 * 1024
MANIFEST_LIMIT = 1024 * 1024
BLOCK = 1024 * 1024
_RESERVED = re.compile(r"CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]", re.I)


def _name(value):
    return (isinstance(value, str) and value not in ("", ".", "..") and value.isprintable()
            and len(value.encode("utf-8")) <= 240 and value[-1] not in " ."
            and not any(c in '<>:"/\\|?*' for c in value)
            and not _RESERVED.fullmatch(value.partition(".")[0].rstrip(" ")))


def _not_link(info):
    return not (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400)


def _sources(paths, active):
    entries, sources, total = [], {}, 0

    def add(path, parent, depth, anchor):
        nonlocal total
        _active(active)
        if depth > 32 or len(entries) >= MAX_ENTRIES or not _name(path.name):
            raise ClipboardError("Arquivos demais, nome incompatível ou pasta profunda demais.")
        info = path.lstat()
        if not _not_link(info) or not path.resolve().is_relative_to(anchor):
            raise ClipboardError("Links e pastas redirecionadas não podem ser copiados.")
        kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else None
        if kind is None:
            raise ClipboardError("Somente arquivos e pastas comuns podem ser copiados.")
        index = len(entries)
        item = dict(id=index, parent=parent, name=path.name, type=kind)
        entries.append(item)
        if kind == "file":
            total += info.st_size
            if total > MAX_FILE_BYTES:
                raise ClipboardError("Arquivos acima de 512 MiB no total.")
            item.update(size=info.st_size, member=f"data/{index:06d}")
            sources[index] = (path, info, anchor)
        else:
            children = []
            with os.scandir(path) as listing:
                for child in listing:
                    if len(children) + len(entries) >= MAX_ENTRIES:
                        raise ClipboardError("Selecione até 256 arquivos e pastas, incluindo o conteúdo.")
                    children.append(Path(child.path))
            children.sort(key=lambda child: child.name.casefold())
            for child in children:
                add(child, index, depth + 1, anchor)

    for value in paths:
        _active(active)
        path = Path(value)
        add(path, None, 0, path.resolve())
    _check_entries(entries)
    return entries, sources


def _check_entries(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ClipboardError("Quantidade de arquivos inválida.")
    siblings, total, depths = set(), 0, {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ClipboardError("Manifesto de arquivos inválido.")
        basic = {"id", "parent", "name", "type"}
        if (type(item.get("id")) is not int or item["id"] != index or not _name(item.get("name"))
                or item.get("type") not in ("file", "directory")):
            raise ClipboardError("Manifesto de arquivos inválido.")
        parent = item.get("parent")
        if parent is not None and (type(parent) is not int or not 0 <= parent < index or entries[parent]["type"] != "directory"):
            raise ClipboardError("Hierarquia de arquivos inválida.")
        depths[index] = depths[parent] + 1 if parent is not None else 0
        if depths[index] > 32:
            raise ClipboardError("Pasta profunda demais.")
        key = (parent, item["name"].casefold())
        if key in siblings:
            raise ClipboardError("Arquivos com nomes repetidos não podem ser copiados juntos.")
        siblings.add(key)
        if item["type"] == "directory":
            if set(item) != basic:
                raise ClipboardError("Manifesto de pasta inválido.")
            continue
        if set(item) != basic | {"size", "sha256", "member"}:
            # During creation the checksum is filled while copying.
            if set(item) != basic | {"size", "member"}:
                raise ClipboardError("Manifesto de arquivo inválido.")
        if (type(item.get("size")) is not int or not 0 <= item["size"] <= MAX_FILE_BYTES
                or item.get("member") != f"data/{index:06d}"):
            raise ClipboardError("Manifesto de arquivo inválido.")
        if "sha256" in item and (not isinstance(item["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", item["sha256"])):
            raise ClipboardError("Checksum de arquivo inválido.")
        total += item["size"]
        if total > MAX_FILE_BYTES:
            raise ClipboardError("Arquivos acima de 512 MiB no total.")
    return total


def _source_copy(archive, entry, source, active):
    path, expected, anchor = source
    _active(active)
    if not path.resolve().is_relative_to(anchor):
        raise ClipboardError("Arquivo redirecionado durante a cópia.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as incoming, archive.open(entry["member"], "w", force_zip64=True) as outgoing:
        current = os.fstat(incoming.fileno())
        if ((current.st_dev, current.st_ino, current.st_size) != (expected.st_dev, expected.st_ino, expected.st_size)
                or not stat.S_ISREG(current.st_mode) or not _not_link(current)):
            raise ClipboardError("Arquivo alterado durante a cópia.")
        digest, remaining = hashlib.sha256(), entry["size"]
        while remaining:
            _active(active)
            data = incoming.read(min(BLOCK, remaining))
            if not data:
                raise ClipboardError("Arquivo alterado durante a cópia.")
            outgoing.write(data)
            digest.update(data)
            remaining -= len(data)
        _active(active)
        after = os.fstat(incoming.fileno())
        if incoming.read(1) or after.st_size != expected.st_size or after.st_mtime_ns != expected.st_mtime_ns:
            raise ClipboardError("Arquivo alterado durante a cópia.")
        entry["sha256"] = digest.hexdigest()


def create_bundle(payload, destination, active):
    _active(active)
    check_payload(payload)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".clipboard-", suffix=".zip", dir=destination.parent)
        os.close(fd)
        temporary = Path(name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            manifest = {"version": 1, "kind": payload.kind}
            if payload.kind == "files":
                entries, sources = _sources(payload.value, active)
                for entry in entries:
                    if entry["type"] == "file":
                        _source_copy(archive, entry, sources[entry["id"]], active)
                manifest["entries"] = entries
            else:
                data = payload.value.encode("utf-8") if payload.kind == "text" else payload.value
                manifest.update(member="data/000000", size=len(data), sha256=hashlib.sha256(data).hexdigest())
                with archive.open("data/000000", "w", force_zip64=True) as output:
                    for position in range(0, len(data), BLOCK):
                        _active(active)
                        output.write(data[position:position + BLOCK])
            _active(active)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        _active(active)
        if temporary.stat().st_size > MAX_BUNDLE_BYTES:
            raise ClipboardError("Área de transferência acima de 512 MiB.")
        os.replace(temporary, destination)
        temporary = None
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeError):
        raise ClipboardError("Não foi possível preparar os itens da área de transferência.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClipboardError("Campo repetido no manifesto.")
        result[key] = value
    return result


def _manifest(archive):
    members = archive.infolist()
    if not 1 <= len(members) <= MAX_ENTRIES + 1 or len({item.filename for item in members}) != len(members):
        raise ClipboardError("Container de área de transferência inválido.")
    infos = {item.filename: item for item in members}
    total = 0
    for info in members:
        mode = info.external_attr >> 16
        if (info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 or info.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.file_size != info.compress_size
                or info.file_size < 0 or info.file_size > MAX_FILE_BYTES):
            raise ClipboardError("Container comprimido, link ou conteúdo inválido.")
        total += info.file_size
    if total > MAX_BUNDLE_BYTES or "manifest.json" not in infos or infos["manifest.json"].file_size > MANIFEST_LIMIT:
        raise ClipboardError("Manifesto ou área de transferência acima do limite.")
    manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_json_pairs)
    if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or manifest.get("kind") not in ("text", "image", "files")):
        raise ClipboardError("Manifesto da área de transferência inválido.")
    expected = {"manifest.json"}
    if manifest["kind"] == "files":
        if set(manifest) != {"version", "kind", "entries"}:
            raise ClipboardError("Manifesto de arquivos inválido.")
        _check_entries(manifest["entries"])
        data_entries = [item for item in manifest["entries"] if item["type"] == "file"]
    else:
        if set(manifest) != {"version", "kind", "member", "size", "sha256"}:
            raise ClipboardError("Manifesto inválido.")
        limit = MAX_BYTES if manifest["kind"] == "text" else IMAGE_LIMIT
        if (manifest["member"] != "data/000000" or type(manifest["size"]) is not int
                or not 0 <= manifest["size"] <= limit):
            raise ClipboardError("Conteúdo acima do limite.")
        data_entries = [manifest]
    for item in data_entries:
        if (not isinstance(item.get("sha256"), str) or not re.fullmatch("[a-f0-9]{64}", item["sha256"])
                or item["member"] not in infos or infos[item["member"]].file_size != item["size"]):
            raise ClipboardError("Manifesto ou tamanho de arquivo inválido.")
        expected.add(item["member"])
    if set(infos) != expected:
        raise ClipboardError("Conteúdo extra no container de área de transferência.")
    return manifest


def _preflight(path):
    """Bound the central directory before ZipFile allocates its entry list."""
    size = path.stat().st_size
    if not 22 <= size <= MAX_BUNDLE_BYTES:
        raise ClipboardError("Container de área de transferência acima do limite ou inválido.")
    with path.open("rb") as source:
        if source.read(4) != b"PK\x03\x04":
            raise ClipboardError("Container de área de transferência inválido.")
        source.seek(max(0, size - 65557))
        tail = source.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise ClipboardError("Diretório ZIP inválido.")
    _, disk, central_disk, local_count, count, directory_size, directory_offset, comment = struct.unpack_from("<4s4H2IH", tail, offset)
    absolute = size - len(tail) + offset
    if (disk or central_disk or local_count != count or not 1 <= count <= MAX_ENTRIES + 1
            or directory_size > MANIFEST_LIMIT or directory_offset + directory_size != absolute
            or offset + 22 + comment != len(tail)):
        raise ClipboardError("Diretório ZIP acima do limite ou inválido.")


def inspect_bundle_kind(path):
    try:
        path = Path(path)
        _preflight(path)
        with zipfile.ZipFile(path) as archive:
            return _manifest(archive)["kind"]
    except (OSError, ValueError, KeyError, TypeError, RecursionError, zipfile.BadZipFile):
        raise ClipboardError("Container de área de transferência inválido.") from None


def _read_entry(archive, entry, active, output=None):
    digest, remaining, data = hashlib.sha256(), entry["size"], bytearray()
    with archive.open(entry["member"]) as source:
        while remaining:
            _active(active)
            chunk = source.read(min(BLOCK, remaining))
            if not chunk:
                raise ClipboardError("Conteúdo incompleto.")
            digest.update(chunk)
            remaining -= len(chunk)
            if output is None:
                data.extend(chunk)
            else:
                output.write(chunk)
        _active(active)
        if source.read(1) or digest.hexdigest() != entry["sha256"]:
            raise ClipboardError("Checksum de área de transferência inválido.")
    return bytes(data)


def apply_bundle(path, receive_root, active):
    _active(active)
    transaction = None
    root = Path(receive_root)
    try:
        path = Path(path)
        _preflight(path)
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive)
            if manifest["kind"] != "files":
                data = _read_entry(archive, manifest, active)
                value = data.decode("utf-8") if manifest["kind"] == "text" else data
                payload = ClipboardPayload(manifest["kind"], value)
                check_payload(payload)
                return payload
            _active(active)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.resolve() != root.parent.resolve() / root.name or not _not_link(root.lstat()):
                raise ClipboardError("Pasta de recebimento redirecionada.")
            root = root.resolve()
            transaction = Path(tempfile.mkdtemp(prefix="clipboard-", dir=root))
            paths, selected = {}, []
            for entry in manifest["entries"]:
                _active(active)
                parent = paths[entry["parent"]] if entry["parent"] is not None else transaction
                target = parent / entry["name"]
                if entry["type"] == "directory":
                    target.mkdir(mode=0o700)
                else:
                    with target.open("xb") as output:
                        _read_entry(archive, entry, active, output)
                paths[entry["id"]] = target
                if entry["parent"] is None:
                    selected.append(str(target))
            _active(active)
            result = ClipboardPayload("files", tuple(selected))
            transaction = None
            return result
    except (OSError, ValueError, KeyError, TypeError, RecursionError, zipfile.BadZipFile, RuntimeError):
        raise ClipboardError("Não foi possível receber os itens da área de transferência.") from None
    finally:
        if transaction is not None and transaction.resolve().is_relative_to(root):
            shutil.rmtree(transaction, ignore_errors=True)

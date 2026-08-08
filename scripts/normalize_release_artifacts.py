#!/usr/bin/env python3
"""Normalize Python release archives for byte-for-byte reproducible builds."""
from __future__ import annotations

import argparse
import copy
import gzip
import io
import os
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

_ZIP_MIN_EPOCH = 315532800  # 1980-01-01, the earliest ZIP timestamp.


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def normalize_sdist(path: str | Path, *, epoch: int) -> Path:
    """Rewrite a tar.gz with stable ownership, timestamps, ordering and gzip metadata."""
    source = Path(path).expanduser().resolve()
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(source, mode="r:gz") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member) if member.isfile() else None
            members.append((copy.copy(member), extracted.read() if extracted else None))

    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for member, data in members:
                member.mtime = epoch
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.pax_headers = {}
                archive.addfile(member, io.BytesIO(data) if data is not None else None)
    _atomic_replace(source, output.getvalue())
    return source


def normalize_wheel(path: str | Path, *, epoch: int) -> Path:
    """Rewrite a wheel with stable ZIP timestamps and metadata without changing contents."""
    source = Path(path).expanduser().resolve()
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(source, mode="r") as archive:
        for info in archive.infolist():
            entries.append((copy.copy(info), archive.read(info.filename)))

    timestamp = time.gmtime(max(epoch, _ZIP_MIN_EPOCH))[:6]
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", allowZip64=True) as archive:
        for original, data in entries:
            info = zipfile.ZipInfo(original.filename, date_time=timestamp)
            info.compress_type = original.compress_type
            info.comment = original.comment
            info.extra = b""
            info.create_system = original.create_system
            info.create_version = original.create_version
            info.extract_version = original.extract_version
            info.external_attr = original.external_attr
            info.internal_attr = original.internal_attr
            info.flag_bits = original.flag_bits & ~0x08
            archive.writestr(info, data)
    _atomic_replace(source, output.getvalue())
    return source


def normalize_artifact(path: str | Path, *, epoch: int) -> Path:
    target = Path(path)
    if target.name.endswith(".tar.gz"):
        return normalize_sdist(target, epoch=epoch)
    if target.suffix == ".whl":
        return normalize_wheel(target, epoch=epoch)
    raise ValueError(f"Unsupported release artifact: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    parser.add_argument(
        "--epoch",
        type=int,
        default=int(os.environ.get("SOURCE_DATE_EPOCH", "0") or 0),
    )
    args = parser.parse_args()
    if args.epoch <= 0:
        parser.error("--epoch or SOURCE_DATE_EPOCH must be a positive Unix timestamp")
    for artifact in args.artifacts:
        print(normalize_artifact(artifact, epoch=args.epoch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

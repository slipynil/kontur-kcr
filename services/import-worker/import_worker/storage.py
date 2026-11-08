import hashlib
import os
from pathlib import Path
from typing import BinaryIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile


class InvalidUpload(ValueError):
    pass


def upload_path(root: Path, batch_id: UUID) -> Path:
    return root / f"{batch_id}.xlsx"


def store_upload(source: BinaryIO, path: Path, limit: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as output:
            while chunk := source.read(1024**2):
                if not size and not chunk.startswith(b"PK\x03\x04"):
                    raise InvalidUpload("Файл должен быть книгой XLSX")
                size += len(chunk)
                if size > limit:
                    raise InvalidUpload("Файл превышает допустимый размер")
                digest.update(chunk)
                output.write(chunk)
            if not size:
                raise InvalidUpload("Файл пуст")
            output.flush()
            os.fsync(output.fileno())
        try:
            with ZipFile(temporary) as archive:
                archive.getinfo("[Content_Types].xml")
                archive.getinfo("xl/workbook.xml")
        except (BadZipFile, KeyError) as exc:
            raise InvalidUpload("Файл должен быть книгой XLSX") from exc
        temporary.replace(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()

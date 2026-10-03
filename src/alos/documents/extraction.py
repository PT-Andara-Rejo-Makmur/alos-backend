"""Bounded TEXT and DOCX extraction with no network access or executable content."""

import io
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

TYPES = {
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def validate_filename(filename: str, content_type: str) -> str:
    if not 1 <= len(filename) <= 180 or not re.fullmatch(r"[\w .()-]+", filename, re.UNICODE):
        raise ValueError("invalid filename")
    if filename in {".", ".."} or filename.startswith(".") or filename.endswith((" ", ".")):
        raise ValueError("invalid filename")
    suffix = "." + filename.rsplit(".", 1)[-1].lower()
    if suffix not in TYPES or content_type != TYPES[suffix]:
        raise ValueError("unsupported document type")
    return "TEXT" if suffix == ".txt" else "DOCX"


def extract(content: bytes, kind: str) -> str:
    if kind == "TEXT":
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("binary data is not text")
    elif kind == "DOCX":
        if not content.startswith(b"PK\x03\x04"):
            raise ValueError("document signature is invalid")
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(item.file_size for item in entries) > 20_000_000:
                raise ValueError("document expansion limit exceeded")
            if any(
                item.flag_bits & 1 or item.file_size > max(1, item.compress_size) * 100
                for item in entries
            ):
                raise ValueError("encrypted or excessively compressed document")
            if any(
                "vba" in item.filename.lower() or "embeddings/" in item.filename.lower()
                for item in entries
            ):
                raise ValueError("executable document content is unsupported")
            xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ValueError("document entities are unsupported")
            # UTF-16/32 can disguise entity declarations, so only standard UTF-8 XML is accepted.
            xml.decode("utf-8-sig")
            if b"\x00" in xml:
                raise ValueError("unsupported XML encoding")
            root = ET.fromstring(xml)  # noqa: S314 - expansion bounded and declarations rejected
            text = "\n".join(
                "".join(node.itertext())
                for node in root.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
                )
            )
    else:
        raise ValueError("unsupported extraction type")
    text = text.strip()
    if not text or len(text) > 200_000:
        raise ValueError("document text is empty or exceeds extraction limit")
    return text

"""Reject hostile containers and ambiguous file types before source registration."""

import io
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from alos.documents.extraction import extract, validate_filename


def docx(xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_docx_extracts_text_and_rejects_entities() -> None:
    xml = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           b'<w:body><w:p><w:r><w:t>Dokumen bisnis</w:t></w:r></w:p></w:body></w:document>')
    assert extract(docx(xml), "DOCX") == "Dokumen bisnis"
    with pytest.raises(ValueError):
        extract(
            docx(b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'),
            "DOCX",
        )
    with pytest.raises(ValueError):
        extract(docx(b"A" * 500_000), "DOCX")


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("../x.txt", "text/plain"),
        ("x.exe", "text/plain"),
        ("x.docx", "text/plain"),
        (".txt", "text/plain"),
        ("x.txt\x00", "text/plain"),
    ],
)
def test_invalid_file_metadata(filename: str, mime: str) -> None:
    with pytest.raises(ValueError):
        validate_filename(filename, mime)

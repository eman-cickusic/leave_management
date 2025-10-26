"""Supporting utilities for reporting and exports."""
from __future__ import annotations

from io import BytesIO
from typing import Iterable, List


def _escape_pdf_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def generate_pdf_report(title: str, lines: Iterable[str]) -> bytes:
    """Produce a very small PDF document containing the provided lines."""

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    objects: List[bytes] = []

    def add_object(content: bytes) -> None:
        objects.append(content)

    add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_object(b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>")
    add_object(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )

    content_lines = [
        "BT",
        "/F1 16 Tf",
        "72 760 Td",
        f"({_escape_pdf_text(title)}) Tj",
        "/F1 12 Tf",
        "16 TL",
    ]
    for line in lines:
        content_lines.append("T*")
        if line:
            content_lines.append(f"({_escape_pdf_text(line)}) Tj")
    content_lines.append("ET")
    content_bytes = ("\n".join(content_lines) + "\n").encode("utf-8")
    content_obj = (
        f"<< /Length {len(content_bytes)} >>\nstream\n".encode("utf-8")
        + content_bytes
        + b"endstream"
    )
    add_object(content_obj)

    add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("utf-8"))
        buffer.write(obj)
        buffer.write(b"\nendobj\n")

    xref_position = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(f"{offset:010} 00000 n \n".encode("utf-8"))
    buffer.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF".encode(
            "utf-8"
        )
    )
    return buffer.getvalue()

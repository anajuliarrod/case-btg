"""PDF -> texto por página (ou imagem, quando não há camada de texto)."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pdfplumber
import pymupdf

from case_btg.config import MIN_TEXT_CHARS_PER_PAGE, SCAN_RENDER_DPI

Source = Literal["texto_nativo", "imagem_escaneada"]


@dataclass
class Page:
    number: int
    text: str
    image_png: bytes | None = None


@dataclass
class Document:
    path: Path
    sha256: str
    source: Source
    pages: list[Page]

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def load_document(path: Path) -> Document:
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text(layout=True) or "" for page in pdf.pages]

    if all(len(t.strip()) >= MIN_TEXT_CHARS_PER_PAGE for t in texts):
        pages = [Page(number=i + 1, text=t) for i, t in enumerate(texts)]
        return Document(path, sha256, "texto_nativo", pages)

    with pymupdf.open(path) as pdf:
        pages = [
            Page(
                number=i + 1,
                text=texts[i],
                image_png=page.get_pixmap(dpi=SCAN_RENDER_DPI).tobytes("png"),
            )
            for i, page in enumerate(pdf)
        ]
    return Document(path, sha256, "imagem_escaneada", pages)

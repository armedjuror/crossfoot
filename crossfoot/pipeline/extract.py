import fitz

from crossfoot.types import ExtractedDoc, Word

MIN_WORDS_PER_PAGE = 5


class BadPasswordError(Exception):
    pass


def extract(file_path: str, password: str | None = None) -> ExtractedDoc:
    doc = fitz.open(file_path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise BadPasswordError("incorrect or missing password")

    pages: list[list[Word]] = []
    first_page_text = ""
    for page_index in range(doc.page_count):
        page = doc[page_index]
        raw_words = page.get_text("words")
        words = [
            Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], page=page_index)
            for w in raw_words
        ]
        pages.append(words)
        if page_index == 0:
            first_page_text = page.get_text()

    is_scanned = doc.page_count == 0 or all(len(p) < MIN_WORDS_PER_PAGE for p in pages)

    return ExtractedDoc(
        pages=pages, page_count=doc.page_count,
        is_scanned=is_scanned, first_page_text=first_page_text,
    )

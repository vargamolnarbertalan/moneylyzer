"""Files the owner attaches to an AI analyst question: turned into something each model can read.

Every file is sniffed by its bytes (the name and the browser's type are hints only) and becomes one part:

- ``text``: plain text, CSV, Markdown, JSON...; Excel (.xlsx, via openpyxl) and Word (.docx, read from its XML)
  as text; a PDF's text too, for models that can't take PDFs.
- ``image``: PNG, JPEG, GIF or WebP, for models that read images.
- ``pdf``: the PDF itself, for models that read PDFs (its text is kept as the fallback).
- ``note``: anything else; the model is told the file was attached but could not be passed on, so it can say so.

Nothing is written to disk: the bytes live in memory for the one request.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import logging
import re
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

MAX_FILES = 5
MAX_TOTAL_BYTES = 6 * 1024 * 1024  # fits Claude login's 10 MB input once base64-encoded
# Any image the total allows: 6 MiB is 8.4 MB base64, under the Claude API's 10 MB (base64) per image (vision
# docs, checked 2026-11-25). Kept as its own limit so a later, larger total can't silently pass it.
MAX_IMAGE_BYTES = MAX_TOTAL_BYTES
MAX_TEXT_CHARS = 200_000  # per file: a statement fits, a whole archive does not
MAX_SHEET_ROWS = 5_000
MAX_SHEET_COLS = 200  # no statement is wider; also bounds a crafted 16,384-column row
MAX_DOCX_XML = 20 * 1024 * 1024  # word/document.xml is parsed whole: a 200-page report is a few MB
UNSUPPORTED = ("a file type this app cannot pass on (it takes text and CSV, .xlsx, .docx, PDF, PNG, JPEG, GIF and "
               "WebP; not old .xls/.doc, password-protected Office files or HEIC photos)")
MAX_PDF_PAGES = 300
# Claude reads a PDF natively up to 600 pages (100 on a 200k-context model) and never an encrypted one
# (PDF support docs, checked 2026-11-25); past that the PDF goes as its text instead.
MAX_NATIVE_PDF_PAGES = 100
MAX_IMAGE_SIDE = 8000  # Claude refuses a larger image (vision docs, checked 2026-11-25), failing the whole question
MAX_UNZIPPED = 100 * 1024 * 1024  # an .xlsx/.docx is a zip: refuse one that would unpack into a memory bomb
IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF87a": "image/gif", b"GIF89a": "image/gif"}
NAME_KEEP = set(" .,()[]-+&@#=%'_")
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

log = logging.getLogger("lia")


class AttachmentError(ValueError):
    pass


class _Note(Exception):
    """A file that can't be passed on, with the reason the model is told."""


@dataclass
class Part:
    kind: str  # text | image | pdf | note
    name: str
    size: int
    media_type: str = ""
    data: bytes = b""
    text: str = ""
    note: str = ""
    truncated: bool = False
    extra: dict = field(default_factory=dict)


def clean_name(name: str) -> str:
    """A file name that is safe to show and to put in a prompt tag."""
    # NFC first: macOS sends "á" as "a" + a combining accent. Letters, digits and combining marks (Devanagari
    # vowel signs...) stay; "<", '"', "/", control characters and the rest become "_".
    name = unicodedata.normalize("NFC", name or "").strip()
    name = re.sub(r"_{2,}", "_", "".join(ch if ch.isalnum() or ch in NAME_KEEP or unicodedata.category(ch).startswith("M")
                                         else "_" for ch in name))
    if len(name) > 120:  # keep the extension: it tells the model (and the owner) what the file was
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + dot + ext[:9]) if dot and len(ext) <= 9 else name[:120]
    return name.strip(" .") or "file"


def decode(items: list[dict]) -> list[tuple[str, bytes]]:
    """[{name, data (base64)}] -> [(name, bytes)], enforcing the count and size limits."""
    if len(items) > MAX_FILES:
        raise AttachmentError(f"Attach at most {MAX_FILES} files per question.")
    out, total = [], 0
    for item in items:
        try:
            raw = base64.b64decode(item.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError(f"{clean_name(item.get('name'))} could not be read; attach it again.") from None
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError(f"Attachments are limited to {MAX_TOTAL_BYTES // (1024 * 1024)} MB per question in total.")
        out.append((clean_name(item.get("name")), raw))
    return out


def _cap(text: str) -> tuple[str, bool]:
    return (text[:MAX_TEXT_CHARS], True) if len(text) > MAX_TEXT_CHARS else (text, False)


def _as_text(raw: bytes) -> str | None:
    """Text if the bytes read as UTF-8 (or UTF-16 with a BOM) and look like text, else None."""
    if raw.startswith(b"\xef\xbb\xbf"):  # Excel's "CSV UTF-8" writes one; then the same checks as without
        raw = raw[3:]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", "replace")  # UTF-32 (also FF FE) decodes to NULs here and fails the check below
    else:
        text = None
    try:
        text = text if text is not None else raw.decode("utf-8")
    except UnicodeDecodeError:
        # UTF-8 with a stray byte, or Windows-1250 (Hungarian bank exports often are)? In real UTF-8 the
        # accented letters decode cleanly and outnumber the broken bytes; in Windows-1250 an accented letter
        # followed by plain text is never valid UTF-8, so the clean ones are (almost) none.
        lenient = raw.decode("utf-8", "replace")
        bad = lenient.count("\ufffd")
        good = sum(1 for ch in lenient if ch > "\x7f" and ch != "\ufffd")
        text = lenient if good > bad else raw.decode("cp1250", "replace")  # cp1250's undefined bytes become U+FFFD
    if not text:
        return ""
    # The whole text, not a sample: text followed by binary must not pass. Spaces count as text (U+00A0 is
    # not "printable" to Python, yet Hungarian exports use it as the thousands separator); a U+FFFD from an
    # undecodable byte does not. Real text is all but 100% clean; binary read as Windows-1250 is ~13% dirty.
    dirty = sum(1 for ch in text if ch == "\ufffd" or not (ch.isprintable() or ch.isspace()))
    return text if dirty <= max(2, len(text) * 0.02) else None  # 2 spare: one stray byte in a short note


def _xlsx_text(raw: bytes) -> str:
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    out = io.StringIO()
    try:
        for sheet in book.worksheets:
            # Read-only mode trusts the sheet's declared size: "A1" from some exporters would hide every other
            # cell, "A1:XFD5000" would pad each row to 16,384 columns. Read the cells that are really there.
            sheet.reset_dimensions()
            out.write(f"## Sheet: {sheet.title}\n")
            writer = csv.writer(out)
            written, wide = 0, False
            # one column past the cap, only to know whether anything was cut (the model must be told)
            for row in sheet.iter_rows(values_only=True, max_col=MAX_SHEET_COLS + 1):
                cells = list(row)
                if len(cells) > MAX_SHEET_COLS:
                    wide = wide or cells[MAX_SHEET_COLS] is not None
                    cells = cells[:MAX_SHEET_COLS]
                while cells and cells[-1] is None:
                    cells.pop()
                if not cells:
                    continue  # empty or only formatted: neither written nor counted
                if written >= MAX_SHEET_ROWS:
                    out.write("(more rows not included)\n")
                    break
                writer.writerow(["" if v is None else v for v in cells])
                written += 1
                if out.tell() > MAX_TEXT_CHARS:  # the caller cuts at this length anyway: stop building
                    return out.getvalue()
            if wide:
                out.write(f"(columns after the {MAX_SHEET_COLS}th not included)\n")
            out.write("\n")
    finally:
        book.close()
    return out.getvalue()


def _docx_text(raw: bytes) -> str:
    """Paragraphs and table rows from word/document.xml (no python-docx/lxml needed).

    Walks the tree itself rather than .iter(): Word stores a text box twice (mc:Choice and mc:Fallback), and a
    nested table's rows must not be read again as rows of the outer table."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.getinfo("word/document.xml").file_size > MAX_DOCX_XML:
            raise _Note(f"a Word file whose text part is over {MAX_DOCX_XML // (1024 * 1024)} MB")
        root = ElementTree.fromstring(z.read("word/document.xml"))
    w = root.tag[:root.tag.index("}") + 1] if root.tag.startswith("{") else ""  # transitional or strict namespace
    fallback, para_tag, table_tag = f"{MC}Fallback", f"{w}p", f"{w}tbl"

    # properties hold no text (w:pPr's w:tabs/w:tab are tab stops, not tabs); w:moveFrom is the old place of
    # a tracked move, which Word writes with ordinary w:t, so reading it too would duplicate the sentence
    skip = {fallback, f"{w}pPr", f"{w}rPr", f"{w}moveFrom"}

    def runs(el, out: list[str]) -> None:
        for child in el:
            tag = child.tag
            if tag == f"{w}t":
                out.append(child.text or "")
            elif tag == f"{w}tab":
                out.append("\t")
            elif tag in (f"{w}br", f"{w}cr"):
                out.append("\n")
            elif tag == f"{w}noBreakHyphen":
                out.append("-")
            elif tag in (para_tag, f"{w}tr"):  # a paragraph or table row inside a text box: its own line
                out.append("\n")
                runs(child, out)
            elif tag == f"{w}tc":
                out.append(" | ")
                runs(child, out)
            elif tag not in skip:
                runs(child, out)

    def para(p) -> str:
        out: list[str] = []
        runs(p, out)
        return "".join(out).strip("\n")  # a text box at the start of its paragraph adds a leading line break

    def children(el, tag: str):  # direct children, also through wrappers such as w:sdt, never into a nested table
        for child in el:
            if child.tag == tag:
                yield child
            elif child.tag not in (para_tag, table_tag, fallback):
                yield from children(child, tag)

    def cell_text(el, parts: list[str]) -> None:  # a cell's paragraphs, a nested table's flattened into it
        for child in el:
            if child.tag == para_tag:
                text = para(child).strip()
                if text:
                    parts.append(text)
            elif child.tag not in skip:
                cell_text(child, parts)

    lines: list[str] = []

    def walk(el) -> None:  # paragraphs and tables in document order, also inside content controls (w:sdt)
        for child in el:
            if child.tag == para_tag:
                text = para(child)
                if text.strip():
                    lines.append(text)
            elif child.tag == table_tag:
                for row in children(child, f"{w}tr"):
                    cells = []
                    for cell in children(row, f"{w}tc"):
                        parts: list[str] = []
                        cell_text(cell, parts)
                        cells.append(" ".join(parts))
                    lines.append(" | ".join(cells))
            elif child.tag not in skip:
                walk(child)

    body = root.find(f"{w}body")
    if body is not None:
        walk(body)
    return "\n".join(lines)


def _pdf_open(raw: bytes):
    """(reader, page count, encrypted). Permission-only encryption (common on bank statements) opens with an
    empty password; a PDF that needs a real password raises."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    encrypted = reader.is_encrypted
    if encrypted:
        reader.decrypt("")
    return reader, len(reader.pages), encrypted


def _pdf_text(raw: bytes) -> str:
    """Each page's text, or "" when no page has any (a scan). A page whose text pypdf can't extract is
    skipped, not fatal. Only run when a route needs the text: it can take seconds on a chart-heavy PDF."""
    reader, _, _ = _pdf_open(raw)
    pages, length, found = [], 0, False
    for i, page in enumerate(reader.pages, 1):
        if i > MAX_PDF_PAGES or length > MAX_TEXT_CHARS:  # the caller cuts at MAX_TEXT_CHARS anyway
            pages.append("(more pages not included)")
            break
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = "(this page's text could not be read)"
        else:
            found = found or bool(text)
        pages.append(f"[Page {i}]\n{text}")
        length += len(pages[-1])
    return "\n\n".join(pages) if found else ""


def pdf_to_text(part: Part, why: str = "a PDF without extractable text (a scan, or encrypted)") -> Part:
    """A PDF part as its text, for a route (or a request) that can't take it as a PDF; a note if it has none."""
    try:
        text = _pdf_text(part.data)
    except Exception:
        text = ""
    if not text:
        return Part("note", part.name, part.size, note=why)
    text, cut = _cap(text)
    return Part("text", part.name, part.size, "text/plain", text=text, truncated=cut, extra={"from": "pdf"})


def fit_request(parts: list[Part]) -> list[Part]:
    """Claude's PDF page limit counts every PDF in the request, not each file: past it, the rest go as text."""
    out, pages = [], 0
    for part in parts:
        if part.kind == "pdf":
            count = part.extra.get("pages", 0)
            if pages + count > MAX_NATIVE_PDF_PAGES:
                part = pdf_to_text(part, why=f"a PDF without extractable text, sent with {pages} pages of other PDFs")
            else:
                pages += count
        out.append(part)
    return out


def _image_size(raw: bytes, media: str) -> tuple[int, int] | None:
    """(width, height) from the image header, or None when it can't be read (then the image isn't sent:
    Claude would refuse a broken image and fail the whole question)."""
    try:
        if media == "image/png":
            return struct.unpack(">II", raw[16:24])
        if media == "image/gif":
            return struct.unpack("<HH", raw[6:10])
        if media == "image/webp":
            chunk = raw[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(raw[27:30], "little")
            if chunk == b"VP8L":
                bits = int.from_bytes(raw[21:25], "little")
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", raw[26:30])
                return w & 0x3FFF, h & 0x3FFF
            return None
        if media == "image/jpeg":
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    return None
                marker = raw[i + 1]
                if marker == 0xFF:  # fill byte before a marker
                    i += 1
                    continue
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
                    i += 2
                    continue
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    except struct.error:
        return None
    return None


def _zip_kind(raw: bytes) -> str | None:
    """xlsx, docx, another zip, or None. A zip that would unpack to more than MAX_UNZIPPED is "bomb"."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
    except Exception:  # BadZipFile, but also NotImplementedError (zip version), UnicodeDecodeError (names)...
        return None
    if sum(i.file_size for i in infos) > MAX_UNZIPPED:
        return "bomb"
    names = {i.filename for i in infos}
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "word/document.xml" in names:
        return "docx"
    return "zip"


def prepare(name: str, raw: bytes) -> Part:
    """One attached file as the part every model route can use. Never raises: a file that can't be read
    becomes a note, so one bad file can't fail the whole question."""
    try:
        return _prepare(name, raw)
    except _Note as why:
        return Part("note", name, len(raw), note=str(why))
    except Exception as exc:
        log.warning("attachment %s could not be read: %s", name, type(exc).__name__)
        return Part("note", name, len(raw), note="a file that could not be read")


def _prepare(name: str, raw: bytes) -> Part:
    size = len(raw)
    media = next((m for magic, m in IMAGE_TYPES.items() if raw.startswith(magic)), None)
    if media is None and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        media = "image/webp"
    if media:
        if size > MAX_IMAGE_BYTES:
            return Part("note", name, size, media, note=f"an image over {MAX_IMAGE_BYTES // (1024 * 1024)} MB, too large to pass on")
        dims = _image_size(raw, media)
        if not dims or min(dims) < 1:
            return Part("note", name, size, media, note="an image that could not be read (damaged, or an unusual format)")
        if max(dims) > MAX_IMAGE_SIDE:
            return Part("note", name, size, media, note=f"an image of {dims[0]}x{dims[1]} pixels, over the {MAX_IMAGE_SIDE} pixel limit")
        return Part("image", name, size, media, data=raw)
    if b"%PDF-" in raw[:1024]:  # readers accept the header anywhere in the first 1024 bytes
        try:
            _, pages, encrypted = _pdf_open(raw)
        except Exception:
            return Part("note", name, size, note="a PDF that could not be opened (damaged, or protected with a password)")
        part = Part("pdf", name, size, "application/pdf", data=raw, extra={"pages": pages})
        # Claude refuses an encrypted PDF and one over its page limit; bytes before the header are untested there
        if encrypted:
            return pdf_to_text(part, why="an encrypted PDF without extractable text")
        if pages > MAX_NATIVE_PDF_PAGES:
            return pdf_to_text(part, why=f"a {pages}-page PDF without extractable text")
        if not raw.startswith(b"%PDF-"):
            return pdf_to_text(part)
        return part
    kind = _zip_kind(raw) if raw.startswith(b"PK") else None
    if kind == "bomb":
        return Part("note", name, size, note="a compressed file that unpacks to more than 100 MB")
    if kind in ("xlsx", "docx"):
        label = "an Excel" if kind == "xlsx" else "a Word"
        try:
            text, cut = _cap(_xlsx_text(raw) if kind == "xlsx" else _docx_text(raw))
        except _Note:
            raise
        except Exception:
            return Part("note", name, size, note=f"{label} file that could not be read")
        if not re.sub(r"## Sheet: [^\n]*", "", text).strip(" \n\r\t|,"):  # only sheet titles and separators
            return Part("note", name, size, note=f"{label} file without any text")
        return Part("text", name, size, "text/plain", text=text, truncated=cut, extra={"from": kind})
    text = None if kind else _as_text(raw)
    if text is not None:
        text, cut = _cap(text)
        return Part("text", name, size, "text/plain", text=text, truncated=cut)
    return Part("note", name, size, note=UNSUPPORTED)


def text_block(part: Part) -> str:
    """The text a model gets for a text part, a note, or (on a route that can't take PDFs) a PDF's text."""
    if part.kind == "pdf":
        part = pdf_to_text(part)
    if part.kind == "note":
        return f'<attachment name="{part.name}" size="{part.size}">\nThe user attached this file, but it could not be passed on: {part.note}. Say so if it matters for the answer.\n</attachment>'
    body = part.text.replace("<", "＜")  # a "</attachment>" inside the file must not end the block
    cut = "\n(The file was longer; the rest was not included.)" if part.truncated else ""
    return f'<attachment name="{part.name}" size="{part.size}">\n{body}{cut}\n</attachment>'


def summary(parts: list[Part]) -> list[dict]:
    """What is kept with a saved conversation: names and sizes, never the contents."""
    return [{"name": p.name, "size": p.size} for p in parts]

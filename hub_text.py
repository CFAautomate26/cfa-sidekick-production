"""Text handling for the Team Hub: a small, safe Markdown subset for SOP
pages, and plain-text extraction from Word documents.

The renderer escapes everything first and only then adds the few tags it
knows (headings, lists, bold/italic, links, the page's own photos), so page
text can never inject HTML or script. Links are limited to http(s), mailto,
tel and Hub pages.
"""

import re
import zipfile
from xml.etree import ElementTree

from markupsafe import Markup, escape

# Control characters, zero-width space and bidi overrides. The zero-width
# (non-)joiners U+200C/U+200D stay: emoji like 🧑‍🍳 are built with them.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200e\u200f"
                      "\u202a-\u202e\u2066-\u2069]")
_SAFE_URL = re.compile(r"^(https?://[^/\\]|mailto:|tel:|/hub/)", re.I)
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)")
_BARE_URL = re.compile(r"(?<![\w\"'=/])https?://[^\s<]{1,500}")
_URL_TAIL = ".,;:!?)'\""
# Spans can't contain their own marker and stop at 300 characters, so a
# long line full of stray ** or _ stays linear to render.
_BOLD = re.compile(r"\*\*(?=[^\s*])([^*\n]{1,300}?)(?<=[^\s*])\*\*")
_ITALIC = re.compile(r"(?<![*\w])_(?=[^\s_])([^_\n]{1,300}?)(?<=[^\s_])_(?![*\w])")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
PHOTO = re.compile(r"^\s*\[photo:\s*(\d{1,3})\]\s*$", re.I)


def clean(text: str) -> str:
    """Normalize newlines and drop control/bidi characters (which could
    disguise a link or reorder what a reader sees)."""
    return _CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))


def _inline(text: str) -> str:
    """Escape one line, then add links and emphasis."""
    out, last = [], 0
    for m in _LINK.finditer(text):
        out.append(_plain(text[last:m.start()]))
        label, url = m.group(1), m.group(2)
        if _SAFE_URL.match(url) and "\\" not in url:
            external = "" if url.startswith("/") else ' target="_blank" rel="noopener"'
            out.append(f'<a href="{escape(url)}"{external}>{_emphasis(str(escape(label)))}</a>')
        else:
            out.append(_plain(m.group(0)))
        last = m.end()
    out.append(_plain(text[last:]))
    return "".join(out)


def _plain(text: str) -> str:
    """Escaped text with bare http(s) URLs linked and emphasis applied
    (never inside a link, where an underscore is part of the address)."""
    escaped = str(escape(text))
    out, last = [], 0
    for m in _BARE_URL.finditer(escaped):
        url = m.group(0).rstrip(_URL_TAIL)
        while url.endswith(("&#34", "&#39")):   # an escaped quote, minus its ';'
            url = url[:-4].rstrip(_URL_TAIL)
        if len(url) <= len("https://"):
            continue
        out.append(_emphasis(escaped[last:m.start()]))
        out.append(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>')
        last = m.start() + len(url)
    out.append(_emphasis(escaped[last:]))
    return "".join(out)


def _emphasis(escaped: str) -> str:
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", escaped)


def render(text: str, photos: dict | None = None) -> Markup:
    """Page body (Markdown subset) -> safe HTML. A line that is just
    "[photo: N]" shows the page's Nth photo: photos maps N -> (url, alt)."""
    html, para, list_kind = [], [], None
    photos = photos or {}

    def flush_para():
        if para:
            html.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_kind
        if list_kind:
            html.append(f"</{list_kind}>")
            list_kind = None

    for raw in clean(text).split("\n"):
        line = raw.rstrip()
        heading, bullet, numbered = (_HEADING.match(line), _BULLET.match(line),
                                     _NUMBERED.match(line))
        photo = PHOTO.match(line)
        if not line.strip():
            flush_para()
            close_list()
        elif photo and int(photo.group(1)) in photos:
            flush_para()
            close_list()
            url, alt = photos[int(photo.group(1))]
            html.append(f'<figure><img src="{escape(url)}" alt="{escape(alt)}" '
                        f'loading="lazy"></figure>')
        elif heading:
            flush_para()
            close_list()
            level = len(heading.group(1)) + 1          # the page title is the h1
            html.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or numbered:
            flush_para()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                html.append(f"<{kind}>")
                list_kind = kind
            html.append(f"<li>{_inline((bullet or numbered).group(1))}</li>")
        else:
            close_list()
            para.append(line.strip())
    flush_para()
    close_list()
    return Markup("\n".join(html))


def plain(text: str) -> str:
    """Page text with the formatting marks removed (search snippets)."""
    out = []
    for line in clean(text).split("\n"):
        line = _HEADING.sub(r"\2", line)
        line = _BULLET.sub(r"\1", line)
        line = _NUMBERED.sub(r"\1", line)
        line = _LINK.sub(r"\1", line)
        line = PHOTO.sub("", line)
        out.append(line.replace("**", ""))
    return " ".join(" ".join(out).split())


# ---------------------------------------------------------------------------
# Word documents
# ---------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_MAX_XML = 10 * 1024 * 1024        # a zip bomb's document.xml never gets read
_MAX_TAGS = 400_000                # nor does one built from millions of tiny elements


def _read_document(source) -> tuple[ElementTree.Element | None, list[str]]:
    try:
        with zipfile.ZipFile(source) as z:
            names = set(z.namelist())
            info = z.getinfo("word/document.xml")
            if info.file_size > _MAX_XML:
                return None, []
            with z.open(info) as f:
                data = f.read(_MAX_XML + 1)
        # Word never writes a DTD; one here can only be an entity bomb.
        if (len(data) > _MAX_XML or b"<!DOCTYPE" in data or b"<!ENTITY" in data
                or data.count(b"<") > _MAX_TAGS):
            return None, []
        root = ElementTree.fromstring(data)
    except Exception:          # damaged, hostile or simply not a Word file
        return None, []
    notes = []
    if "word/comments.xml" in names:
        notes.append("it had comments (left out — check nothing important was in them)")
    if root.find(f".//{_W}ins") is not None or root.find(f".//{_W}del") is not None:
        notes.append("it had tracked changes (insertions kept, deletions left out)")
    return root, notes


def _run_text(el) -> str:
    """Visible text under el, skipping hidden runs, deleted text and nested
    paragraphs (text boxes are read as their own paragraphs)."""
    parts = []
    for child in el:
        tag = child.tag
        if tag in (f"{_W}p", f"{_W}txbxContent", f"{_W}del", f"{_MC}Fallback"):
            continue
        if tag == f"{_W}r" and _hidden(child):
            continue
        if tag == f"{_W}t":
            parts.append(child.text or "")
        elif tag == f"{_W}tab":
            parts.append(" ")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append(" ")
        else:
            parts.append(_run_text(child))
    return "".join(parts)


def _hidden(run) -> bool:
    """A run Word hides (<w:vanish/>, but not <w:vanish w:val="0"/>, which
    shows a run whose style is hidden)."""
    props = run.find(f"{_W}rPr")
    vanish = props.find(f"{_W}vanish") if props is not None else None
    return vanish is not None and (vanish.get(f"{_W}val") or "true").lower() not in (
        "0", "false", "off")


def _paragraphs(el):
    """Paragraphs under el in order — not the ones inside text boxes (read
    separately) or Word's fallback copies of them."""
    for child in el:
        if child.tag == f"{_W}p":
            yield child
        elif child.tag not in (f"{_W}txbxContent", f"{_MC}Fallback", f"{_W}del"):
            yield from _paragraphs(child)


def _text_boxes(el):
    """Text boxes inside a paragraph — once each: Word stores a second,
    fallback copy of every box for older readers."""
    for child in el:
        if child.tag == f"{_MC}Fallback":
            continue
        if child.tag == f"{_W}txbxContent":
            yield child
        elif child.tag != f"{_W}p":
            yield from _text_boxes(child)


def docx_import(source) -> tuple[str, list[str]]:
    """A .docx file's text as the Markdown subset — Word headings become
    headings, list paragraphs bullets, table rows "a · b · c" bullets — plus
    notes about anything left out. ("", []) when it isn't a readable Word
    document. source: a path or a binary file object."""
    root, notes = _read_document(source)
    if root is None:
        return "", []
    lines: list[str] = []

    def new_block():
        if lines and lines[-1]:
            lines.append("")

    def paragraph(p):
        text = " ".join(clean(_run_text(p)).split())
        props = p.find(f"{_W}pPr")
        if not text:
            new_block()
        elif props is not None and props.find(f"{_W}numPr") is not None:
            lines.append(f"- {text}")
        else:
            st = props.find(f"{_W}pStyle") if props is not None else None
            style = (st.get(f"{_W}val") or "") if st is not None else ""
            heading = re.match(r"(?i)heading\s*([1-3])$", style)
            level = int(heading.group(1)) if heading else (
                1 if style.lower() == "title" else 0)
            new_block()
            lines.append("#" * level + " " + text if level else text)
            lines.append("")
        for box in _text_boxes(p):            # text boxes anchored here
            walk(box)

    def table(tbl):
        new_block()
        for row in tbl.findall(f"{_W}tr"):
            cells = []
            for cell in row.findall(f"{_W}tc"):
                parts = []
                for p in _paragraphs(cell):
                    parts.append(_run_text(p))
                    for box in _text_boxes(p):
                        parts += [_run_text(q) for q in _paragraphs(box)]
                words = " ".join(" ".join(clean(part).split()) for part in parts)
                if words.strip():
                    cells.append(" ".join(words.split()))
            if cells:
                lines.append("- " + " · ".join(cells))
        new_block()

    def walk(el):
        for child in el:
            if child.tag == f"{_W}p":
                paragraph(child)
            elif child.tag == f"{_W}tbl":
                table(child)
            elif child.tag != f"{_W}del":
                walk(child)

    body = root.find(f"{_W}body")
    try:
        walk(body if body is not None else root)
    except RecursionError:                     # absurdly deep nesting
        return "", []
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text), notes


def docx_text(source) -> str:
    return docx_import(source)[0]

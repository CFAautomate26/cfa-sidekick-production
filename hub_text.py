"""Text handling for the Team Hub: a small, safe Markdown subset for SOP
pages, and plain-text extraction from Word documents.

The renderer escapes everything first and only then adds the few tags it
knows (headings, lists, bold/italic, links), so page text can never inject
HTML or script. Links are limited to http(s), mailto, tel and in-app paths.
"""

import re
import zipfile
from xml.etree import ElementTree

from markupsafe import Markup, escape

_SAFE_URL = re.compile(r"^(https?://|mailto:|tel:|/)", re.I)
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)")
_BARE_URL = re.compile(r"(?<![\"'=>/])\bhttps?://[^\s<]{1,500}")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])_(?=\S)(.+?)(?<=\S)_(?![*\w])")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")


def _inline(text: str) -> str:
    """Escape one line, then add links and emphasis."""
    out, last = [], 0
    for m in _LINK.finditer(text):
        out.append(_plain(text[last:m.start()]))
        label, url = m.group(1), m.group(2)
        if _SAFE_URL.match(url) and not url.startswith("//") and "\\" not in url:
            external = "" if url.startswith("/") else ' target="_blank" rel="noopener"'
            out.append(f'<a href="{escape(url)}"{external}>{_emphasis(str(escape(label)))}</a>')
        else:
            out.append(_plain(m.group(0)))
        last = m.end()
    out.append(_plain(text[last:]))
    return "".join(out)


def _plain(text: str) -> str:
    """Escaped text with bare http(s) URLs linked and emphasis applied."""
    escaped = str(escape(text))
    escaped = _BARE_URL.sub(
        lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener">{m.group(0)}</a>',
        escaped)
    return _emphasis(escaped)


def _emphasis(escaped: str) -> str:
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", escaped)


def render(text: str) -> Markup:
    """Page body (Markdown subset) -> safe HTML."""
    html, para, list_kind = [], [], None

    def flush_para():
        if para:
            html.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_kind
        if list_kind:
            html.append(f"</{list_kind}>")
            list_kind = None

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        heading, bullet, numbered = (_HEADING.match(line), _BULLET.match(line),
                                     _NUMBERED.match(line))
        if not line.strip():
            flush_para()
            close_list()
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


# ---------------------------------------------------------------------------
# Word documents
# ---------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_XML = 20 * 1024 * 1024        # a zip bomb's document.xml never gets read


def docx_text(path: str) -> str:
    """A .docx file's text as the Markdown subset: Word headings become
    headings, list paragraphs become bullets. Returns "" when the file isn't
    a readable Word document."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > _MAX_XML:
                return ""
            root = ElementTree.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError):
        return ""
    lines = []

    def new_block():
        if lines and lines[-1]:
            lines.append("")

    for p in root.iter(f"{_W}p"):
        text = " ".join("".join(t.text or "" for t in p.iter(f"{_W}t")).split())
        if not text:
            new_block()
            continue
        props = p.find(f"{_W}pPr")
        style = ""
        if props is not None:
            if props.find(f"{_W}numPr") is not None:
                lines.append(f"- {text}")
                continue
            st = props.find(f"{_W}pStyle")
            style = (st.get(f"{_W}val") or "") if st is not None else ""
        heading = re.match(r"(?i)heading\s*([1-3])$", style)
        level = int(heading.group(1)) if heading else (1 if style.lower() == "title" else 0)
        new_block()
        lines.append("#" * level + " " + text if level else text)
        lines.append("")
    return "\n".join(lines).strip()

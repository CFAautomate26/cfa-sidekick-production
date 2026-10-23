"""Text handling for the Team Hub: a small, safe Markdown subset for SOP
pages, and plain-text extraction from Word documents.

The renderer escapes everything first and only then adds the few tags it
knows (headings, lists, bold/italic, links, the page's own photos), so page
text can never inject HTML or script. Links are limited to http(s), mailto,
tel and Hub pages.
"""

import io
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
_BARE_URL = re.compile(r"(?<![\w\"'=/])https?://[^\s<>\"]{1,500}")
_URL_TAIL = ".,;:!?)'\"*_"
_TOKEN = re.compile("\x00(\\d+)\x00")         # a finished link, held aside
_LINE_BREAKS = re.compile("[\u2028\u2029\x85]")
# Spans can't contain their own marker and stop at 300 characters, so a
# long line full of stray ** or _ stays linear to render.
_BOLD = re.compile(r"\*\*(?=[^\s*])([^*\n]{1,300}?)(?<=[^\s*])\*\*")
_ITALIC = re.compile(r"(?<![*\w])_(?=[^\s_])([^_\n]{1,300}?)(?<=[^\s_])_(?![*\w])")
_STRIKE = re.compile(r"~~(?=[^\s~])([^~\n]{1,300}?)(?<=[^\s~])~~")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
# A table is consecutive lines "| cell | cell |"; a "|---|---|" line after the
# first row makes that row the header. "\|" is a literal pipe inside a cell.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_ESCAPED_PIPE = "\x01"
MAX_COLUMNS = 20
_BULLET = re.compile(r"^( *)[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^( *)\d{1,3}[.)]\s+(.*)$")
MAX_LIST_DEPTH = 4                           # two spaces of indent per level
PHOTO = re.compile(r"^\s*\[photo:\s*(\d{1,3})\]\s*$", re.I)


def clean(text: str) -> str:
    """Normalize newlines and drop control/bidi characters (which could
    disguise a link or reorder what a reader sees)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", _LINE_BREAKS.sub("\n", text))


def _inline(text: str) -> str:
    """One line -> safe HTML. Links are built first (from the raw text, so
    an address never carries an escaped entity or a ** marker) and held
    aside as tokens; the rest is escaped, emphasis is applied (it can wrap a
    link, never reach inside one), then the links go back in."""
    links: list[str] = []

    def hold(html: str) -> str:
        links.append(html)
        return f"\x00{len(links) - 1}\x00"

    def markdown_link(m) -> str:
        label, url = m.group(1), m.group(2)
        if not _SAFE_URL.match(url) or "\\" in url:
            return m.group(0)
        external = "" if url.startswith("/") else ' target="_blank" rel="noopener"'
        return hold(f'<a href="{escape(url)}"{external}>{_emphasis(str(escape(label)))}</a>')

    def bare_link(m) -> str:
        url = m.group(0).rstrip(_URL_TAIL)
        if len(url) <= len("https://"):
            return m.group(0)
        return hold(f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(url)}</a>'
                    ) + m.group(0)[len(url):]

    text = _BARE_URL.sub(bare_link, _LINK.sub(markdown_link, text))
    html = _emphasis(str(escape(text)))
    return _TOKEN.sub(lambda m: links[int(m.group(1))], html)


def _emphasis(escaped: str) -> str:
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _STRIKE.sub(r"<s>\1</s>", escaped)
    return _ITALIC.sub(r"<em>\1</em>", escaped)


def _cells(row: str) -> list[str]:
    r"""The cells of a "| a | b |" line (a "\|" stays a pipe)."""
    inner = row.strip().replace("\\|", _ESCAPED_PIPE)
    inner = inner[1:-1] if inner.startswith("|") and inner.endswith("|") else inner
    return [c.strip().replace(_ESCAPED_PIPE, "|") for c in inner.split("|")][:MAX_COLUMNS]


def _cell_html(cell: str) -> str:
    """A cell's HTML; a bullet mid-cell ("a • b") starts a new line."""
    return _inline(cell).replace(" • ", "<br>• ")


def _table(rows: list[str]) -> str:
    """Consecutive table lines -> a scrollable table. The first row is the
    header when the second line is a |---| separator."""
    header = None
    if len(rows) >= 2 and _TABLE_SEP.match(rows[1]):
        header, rows = _cells(rows[0]), rows[2:]
    parsed = [_cells(r) for r in rows if not _TABLE_SEP.match(r)]
    if header is None and not parsed:
        return ""
    width = max([len(header or [])] + [len(r) for r in parsed])
    html = ['<div class="tbl"><table>']
    if header is not None:
        html.append("<thead><tr>" + "".join(
            f"<th>{_inline(c)}</th>" for c in header + [""] * (width - len(header)))
            + "</tr></thead>")
    if parsed:
        html.append("<tbody>")
        for r in parsed:
            html.append("<tr>" + "".join(
                f"<td>{_cell_html(c)}</td>" for c in r + [""] * (width - len(r))) + "</tr>")
        html.append("</tbody>")
    html.append("</table></div>")
    return "\n".join(html)


def render(text: str, photos: dict | None = None) -> Markup:
    """Page body (Markdown subset) -> safe HTML. A line that is just
    "[photo: N]" shows the page's Nth photo: photos maps N -> (url, alt).
    List items indented by two spaces per level nest inside the item above."""
    html, para, table = [], [], []
    lists: list[str] = []            # open lists, outermost first: "ul" / "ol"
    item_open = False                # the innermost list's last <li> is still open
    photos = photos or {}

    def flush_para():
        if para:
            html.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    def flush_table():
        if table:
            html.append(_table(table))
            table.clear()

    def close_item():
        nonlocal item_open
        if item_open:
            html[-1] += "</li>"
            item_open = False

    def close_list(down_to: int = 0):
        nonlocal item_open
        while len(lists) > down_to:
            close_item()
            html.append(f"</{lists.pop()}>")
            item_open = bool(lists)      # back inside the parent's open item

    def list_item(kind: str, depth: int, content: str):
        nonlocal item_open
        depth = min(depth, MAX_LIST_DEPTH, len(lists))     # can't skip a level
        if len(lists) > depth + 1:
            close_list(depth + 1)
        if len(lists) == depth + 1 and lists[-1] != kind:
            close_list(depth)
        if len(lists) == depth + 1:
            close_item()
        else:
            html.append(f"<{kind}>")
            lists.append(kind)
        html.append(f"<li>{content}")
        item_open = True

    for raw in clean(text).split("\n"):
        line = raw.rstrip()
        heading, bullet, numbered = (_HEADING.match(line), _BULLET.match(line),
                                     _NUMBERED.match(line))
        photo = PHOTO.match(line)
        if _TABLE_ROW.match(line) or (table and _TABLE_SEP.match(line)):
            flush_para()
            close_list()
            table.append(line)
            continue
        flush_table()
        if not line.strip():
            flush_para()
            close_list()
        elif photo:
            flush_para()
            close_list()
            if int(photo.group(1)) in photos:        # a removed photo shows nothing
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
            m = bullet or numbered
            list_item("ul" if bullet else "ol", len(m.group(1)) // 2, _inline(m.group(2)))
        else:
            close_list()
            para.append(line.strip())
    flush_para()
    flush_table()
    close_list()
    return Markup("\n".join(html))


def plain(text: str) -> str:
    """Page text with the formatting marks removed (search snippets)."""
    out = []
    for line in clean(text).split("\n"):
        line = _HEADING.sub(r"\2", line)
        line = _BULLET.sub(r"\2", line)
        line = _NUMBERED.sub(r"\2", line)
        line = _LINK.sub(r"\1", line)
        line = PHOTO.sub("", line)
        if _TABLE_SEP.match(line):
            continue
        if _TABLE_ROW.match(line):
            line = " ".join(_cells(line))
        out.append(line.replace("**", "").replace("~~", ""))
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
        if len(data) > _MAX_XML:
            return None, []
        # Word always writes UTF-8. Checking and parsing the decoded text
        # (not the bytes) means no declared encoding can hide a DTD — and
        # Word never writes one, so a DTD here can only be an entity bomb.
        text = data.decode("utf-8").lstrip("\ufeff")
        if ("\x00" in text or "<!DOCTYPE" in text or "<!ENTITY" in text
                or text.count("<") > _MAX_TAGS):
            return None, []
        root = ElementTree.fromstring(text)
    except Exception:          # damaged, hostile or simply not a Word file
        return None, []
    notes = []
    if "word/comments.xml" in names:
        notes.append("it had comments (left out — check nothing important was in them)")
    if any(root.find(f".//{_W}{tag}") is not None
           for tag in ("ins", "del", "moveFrom", "moveTo")):
        notes.append("it had tracked changes (the latest wording was kept)")
    return root, notes


def _run_text(el) -> str:
    """Visible text under el, skipping hidden runs, deleted text and nested
    paragraphs (text boxes are read as their own paragraphs)."""
    parts = []
    for child in el:
        tag = child.tag
        if tag in (f"{_W}p", f"{_W}txbxContent", f"{_W}del", f"{_W}moveFrom",
                   f"{_MC}Fallback"):
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
        elif child.tag not in (f"{_W}txbxContent", f"{_MC}Fallback", f"{_W}del",
                               f"{_W}moveFrom", f"{_W}tbl"):
            yield from _paragraphs(child)
        elif child.tag == f"{_W}tbl":
            for row in _wrapped(child, "tr"):
                for cell in _wrapped(row, "tc"):
                    yield from _paragraphs(cell)


def _wrapped(el, tag: str):
    """el's `tag` children, including those Word wraps in content controls
    (repeating sections) or custom XML."""
    for child in el:
        if child.tag == f"{_W}{tag}":
            yield child
        elif child.tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml"):
            yield from _wrapped(child, tag)


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
    headings, list paragraphs bullets, tables "| a | b |" rows — plus
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
        first = True
        for row in _wrapped(tbl, "tr"):
            cells = []
            for cell in _wrapped(row, "tc"):
                parts = []
                for p in _paragraphs(cell):
                    parts.append(_run_text(p))
                    for box in _text_boxes(p):
                        parts += [_run_text(q) for q in _paragraphs(box)]
                words = " ".join(" ".join(clean(part).split()) for part in parts)
                cells.append(" ".join(words.split()).replace("|", "\\|"))
            if any(cells):
                lines.append("| " + " | ".join(cells) + " |")
                if first:
                    lines.append("|" + "---|" * len(cells))
                first = False
        new_block()

    def walk(el):
        for child in el:
            if child.tag == f"{_W}p":
                paragraph(child)
            elif child.tag == f"{_W}tbl":
                table(child)
            elif child.tag not in (f"{_W}del", f"{_W}moveFrom"):
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


# ---------------------------------------------------------------------------
# Library bundles (many pages at once, from a zip of .md files)
# ---------------------------------------------------------------------------

BUNDLE_MAX_FILES = 400
BUNDLE_MAX_FILE_BYTES = 400_000          # one page's text, before the BODY_MAX check
BUNDLE_MAX_TOTAL_BYTES = 12 * 1024 * 1024
_FRONT_KEYS = ("category", "section", "title", "summary", "keywords", "publish",
               "leaders_only", "required")
PAGE_BREAK = re.compile(r"^={5,}\s*$", re.M)        # between pages in a one-file bundle


def bundle_entries(data: bytes, filename: str = "") -> tuple[list[dict], list[str]]:
    """A bundle -> page entries, plus notes about anything skipped. A bundle
    is a zip of .md pages, or one UTF-8 text file holding the same pages
    separated by a line of five or more "=" signs (phones unzip downloads
    and then refuse to pick the folder, so the one-file form matters).
    Each page starts with a header between "---" lines:

        ---
        category: BOH Operations
        section: Chicken
        title: Breading
        summary: One line shown in the list (optional)
        keywords: also-called words for search (optional)
        publish: yes            (no -> stays a draft)
        leaders_only: no
        ---
        page text in the Markdown subset

    Files are read in name order, so a number at the front of each name
    sets the order pages are created in. Sizes are checked before anything
    is read, so a bundle can't be a zip bomb."""
    entries: list[dict] = []
    notes: list[str] = []
    if not data.startswith(b"PK"):
        return _text_bundle(data, filename or "pages")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, ValueError, OSError):
        return [], [f"{filename or 'that file'}: not a zip or text bundle"]
    if len(infos) > BUNDLE_MAX_FILES:
        return [], [f"too many files (the limit is {BUNDLE_MAX_FILES})"]
    if sum(i.file_size for i in infos) > BUNDLE_MAX_TOTAL_BYTES:
        return [], ["the bundle is too big once unpacked"]
    for info in sorted(infos, key=lambda i: i.filename):
        name = info.filename
        base = name.rsplit("/", 1)[-1]
        if base.startswith(".") or "__MACOSX" in name:
            continue
        if not base.lower().endswith(".md"):
            notes.append(f"{base}: not a .md file")
            continue
        if info.file_size > BUNDLE_MAX_FILE_BYTES:
            notes.append(f"{base}: too big for one page")
            continue
        try:
            text = clean(zf.read(info).decode("utf-8"))
        except (UnicodeDecodeError, zipfile.BadZipFile, OSError):
            notes.append(f"{base}: not readable UTF-8 text")
            continue
        entry, why = _front_matter(text)
        if why:
            notes.append(f"{base}: {why}")
            continue
        entry["name"] = base
        entries.append(entry)
    return entries, notes


def _text_bundle(data: bytes, filename: str) -> tuple[list[dict], list[str]]:
    """One text file: pages separated by ===== lines (or a single page)."""
    if len(data) > BUNDLE_MAX_TOTAL_BYTES:
        return [], [f"{filename}: too big"]
    try:
        text = clean(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return [], [f"{filename}: not readable UTF-8 text (a zip bundle must be a .zip file)"]
    entries, notes = [], []
    chunks = [c.strip() for c in PAGE_BREAK.split(text)]
    chunks = [c for c in chunks if c]
    if len(chunks) > BUNDLE_MAX_FILES:
        return [], [f"{filename}: too many pages (the limit is {BUNDLE_MAX_FILES})"]
    for n, chunk in enumerate(chunks, 1):
        if len(chunk.encode("utf-8")) > BUNDLE_MAX_FILE_BYTES:
            notes.append(f"{filename} page {n}: too big for one page")
            continue
        entry, why = _front_matter(chunk)
        if why:
            notes.append(f"{filename} page {n}: {why}")
            continue
        entry["name"] = f"{filename} page {n}"
        entries.append(entry)
    return entries, notes


def _front_matter(text: str) -> tuple[dict, str | None]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, "no header between --- lines"
    fields = {k: "" for k in _FRONT_KEYS}
    end = None
    for i, line in enumerate(lines[1:60], 1):
        if line.strip() == "---":
            end = i
            break
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in fields:
            fields[key] = " ".join(value.split())
    if end is None:
        return {}, "header never closed with ---"
    body = "\n".join(lines[end + 1:]).strip()
    if not fields["title"] or not fields["category"]:
        return {}, "header needs a title and a category"
    if not body:
        return {}, "no page text under the header"
    yes = {"yes", "y", "true", "1", "on"}
    return {
        "category": fields["category"][:60], "section": fields["section"][:60],
        "title": fields["title"][:140], "summary": fields["summary"][:300],
        "keywords": fields["keywords"][:300], "body": body,
        "publish": fields["publish"].lower() in yes,
        "leaders_only": fields["leaders_only"].lower() in yes,
        "required": fields["required"].lower() in yes,
    }, None

"""Team Hub text handling: the safe Markdown subset and Word extraction."""

import zipfile

import hub_text


def test_render_basics():
    html = str(hub_text.render(
        "# Opening\nArrive **15 min** early at the _back office_.\n\n"
        "- Unlock the back door\n- Fryers on\n1. Count the till\n2. Log the deposit"))
    assert "<h2>Opening</h2>" in html
    assert "<strong>15 min</strong>" in html and "<em>back office</em>" in html
    assert "<ul>\n<li>Unlock the back door</li>\n<li>Fryers on</li>\n</ul>" in html
    assert "<ol>\n<li>Count the till</li>\n<li>Log the deposit</li>\n</ol>" in html


def test_render_nested_lists():
    html = str(hub_text.render(
        "- Filets\n  - One at a time\n    - Tips to the side\n  - Shake off coater\n- Nuggets\n"
        "1. First\n  - sub\n2. Second\n\n- Loose"))
    assert html == (
        "<ul>\n<li>Filets\n<ul>\n<li>One at a time\n<ul>\n<li>Tips to the side</li>\n</ul></li>\n"
        "<li>Shake off coater</li>\n</ul></li>\n<li>Nuggets</li>\n</ul>\n"
        "<ol>\n<li>First\n<ul>\n<li>sub</li>\n</ul></li>\n<li>Second</li>\n</ol>\n"
        "<ul>\n<li>Loose</li>\n</ul>")
    # a level can't be skipped, depth is capped, and the old flat lists still render flat
    html = str(hub_text.render("- a\n          - deep\n- b"))
    assert html.count("<ul>") == 2 and "<li>deep</li>" in html
    assert str(hub_text.render("- a\n- b")) == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"
    assert hub_text.plain("- a\n  - b\n1. c") == "a b c"


def test_render_never_emits_unsafe_html_or_links():
    html = str(hub_text.render(
        '<script>alert(1)</script> <img src=x onerror=alert(1)> '
        '[a](javascript:alert(1)) [b](//evil.com) [c](/\\evil.com) '
        '[d](data:text/html,x) [e](https://ok.example/x"onclick="y)'))
    assert "<script" not in html and "<img" not in html
    assert 'href="javascript' not in html and 'href="//' not in html
    assert 'href="/\\' not in html and 'href="data' not in html
    assert 'onclick="' not in html


def test_render_links():
    html = str(hub_text.render("See [the handbook](/hub/page/3) or https://cfa.example/a_b?x=1&y=2"))
    assert '<a href="/hub/page/3">the handbook</a>' in html
    assert ('<a href="https://cfa.example/a_b?x=1&amp;y=2" target="_blank" '
            'rel="noopener">') in html


def _docx(path, body_xml):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   f'<?xml version="1.0"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>')
    return str(path)


def _p(text, style=None, numbered=False):
    props = ""
    if style or numbered:
        props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + (
            "<w:numPr/>" if numbered else "") + "</w:pPr>"
    return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"


def test_docx_text(tmp_path):
    path = _docx(tmp_path / "sop.docx", "".join([
        _p("Closing the Store", "Title"), _p("Front counter", "Heading1"),
        _p("Count the drawers."), _p("Lock the safe."),
        _p("Wipe tables", numbered=True), _p("Mop floors", numbered=True)]))
    assert hub_text.docx_text(path) == (
        "# Closing the Store\n\n# Front counter\n\nCount the drawers.\n\n"
        "Lock the safe.\n\n- Wipe tables\n- Mop floors")


def test_docx_text_rejects_non_word_files(tmp_path):
    bad = tmp_path / "x.docx"
    bad.write_bytes(b"not a zip")
    assert hub_text.docx_text(str(bad)) == ""
    with zipfile.ZipFile(tmp_path / "y.docx", "w") as z:
        z.writestr("other.xml", "<a/>")
    assert hub_text.docx_text(str(tmp_path / "y.docx")) == ""


def test_render_tables_and_strikethrough():
    html = str(hub_text.render(
        "| **Product** | Hold time |\n|---|---|\n| Nuggets | 20 min \\| not more |\n"
        "| ~~1.25\"~~ **1.0\"** | _x_ |\n\nAfter.\n| no header | row |"))
    assert '<div class="tbl"><table>' in html
    assert "<thead><tr><th><strong>Product</strong></th><th>Hold time</th></tr></thead>" in html
    assert "<td>20 min | not more</td>" in html                 # \| is a literal pipe
    assert "<td><s>1.25&#34;</s> <strong>1.0&#34;</strong></td>" in html
    assert "<p>After.</p>" in html
    assert html.count("<table>") == 2 and html.count("<thead>") == 1
    assert "<tr><td>no header</td><td>row</td></tr>" in html
    # bullets inside a cell each start a line
    html = str(hub_text.render("| Inside | Outside |\n| • Traps checked • Bait replaced | • Door sweeps |"))
    assert "<td>• Traps checked<br>• Bait replaced</td><td>• Door sweeps</td>" in html
    # a lone pipe in prose is not a table, and tables never carry HTML through
    assert "<table>" not in str(hub_text.render("a | b"))
    assert "<img" not in str(hub_text.render("| <img src=x onerror=alert(1)> | x |"))
    assert "<s>" not in str(hub_text.render("~~ not ~~ and ~~~~"))


def test_plain_drops_table_and_strike_marks():
    plain = hub_text.plain("| **Product** | Hold time |\n|---|---|\n| Nuggets | ~~20~~ 30 min |")
    assert plain == "Product Hold time Nuggets 20 30 min"


def test_word_import_tables_become_pipe_rows(tmp_path):
    rows = ("<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Product</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Hold | time</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>Nuggets</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>20 min</w:t></w:r></w:p></w:tc></w:tr></w:tbl>")
    text, _ = hub_text.docx_import(_docx(tmp_path / "t.docx", rows))
    assert text == "| Product | Hold \\| time |\n|---|---|\n| Nuggets | 20 min |"
    html = str(hub_text.render(text))
    assert "<th>Product</th><th>Hold | time</th>" in html and "<td>Nuggets</td>" in html


def _bundle(files):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def _md(category="BOH Operations", title="Breading", body="- Keep the lid closed", **kw):
    head = {"category": category, "title": title, "section": "Chicken", "publish": "yes", **kw}
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in head.items()) + "\n---\n" + body


def test_bundle_entries_reads_headers_in_name_order_and_skips_bad_files():
    entries, notes = hub_text.bundle_entries(_bundle({
        "02-second.md": _md(title="Second"),
        "01-first.md": _md(title="First", summary="  two   words ", leaders_only="yes"),
        "notes.txt": "x", "no-header.md": "just text", "__MACOSX/._01-first.md": "junk",
        "unclosed.md": "---\ntitle: X\ncategory: Y\nbody", "empty.md": _md(title="E", body=""),
    }))
    assert [e["title"] for e in entries] == ["First", "Second"]
    first = entries[0]
    assert first["category"] == "BOH Operations" and first["section"] == "Chicken"
    assert first["summary"] == "two words" and first["leaders_only"] and first["publish"]
    assert first["body"] == "- Keep the lid closed" and first["name"] == "01-first.md"
    assert set(notes) == {"notes.txt: not a .md file", "no-header.md: no header between --- lines",
                          "unclosed.md: header never closed with ---",
                          "empty.md: no page text under the header"}


def test_bundle_entries_refuses_what_it_shouldnt_read():
    assert hub_text.bundle_entries(b"not a zip") == ([], ["that isn't a zip file"])
    big = _bundle({"a.md": _md(body="x" * (hub_text.BUNDLE_MAX_FILE_BYTES + 1))})
    entries, notes = hub_text.bundle_entries(big)
    assert entries == [] and notes == ["a.md: too big for one page"]
    many = _bundle({f"{i}.md": _md(title=str(i)) for i in range(hub_text.BUNDLE_MAX_FILES + 1)})
    assert hub_text.bundle_entries(many)[0] == []

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

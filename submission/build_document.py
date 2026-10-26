"""Build the five-page homework HTML using only Python's standard library.

Run from the repository: uv run python submission/build_document.py
Print the resulting HTML as A4 with browser headers/footers disabled.
"""

from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def inline(text):
    text = escape(text)
    text = re.sub(r"\[([^\]]+)\]\((https://[^)]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def render_page(source):
    lines = source.strip().splitlines()
    result = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = lines[index].strip().strip("|").split("|")
                if not all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells):
                    rows.append([inline(cell.strip()) for cell in cells])
                index += 1
            header, *body = rows
            result.append("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in header)
                          + "</tr></thead><tbody>" + "".join(
                              "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                              for row in body) + "</tbody></table>")
            continue
        heading = re.match(r"^(#{1,3}) (.+)$", line)
        if heading:
            level = len(heading[1])
            result.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        else:
            paragraph = [line]
            while index + 1 < len(lines) and lines[index + 1].strip():
                if lines[index + 1].startswith(("#", "|")):
                    break
                index += 1
                paragraph.append(lines[index].strip())
            result.append("<p>" + inline(" ".join(paragraph)) + "</p>")
        index += 1
    return "\n".join(result)


def main():
    pages = (ROOT / "research.md").read_text().split("<!-- PAGE -->")
    if len(pages) != 5:
        raise SystemExit("Expected exactly five explicitly separated pages")
    sections = "\n".join(
        f'<section class="page"><article>{render_page(page)}</article>'
        f'<footer>Оля й Марко · Продуктове рішення · 26.10.2026 <span>{i} / 5</span></footer></section>'
        for i, page in enumerate(pages, 1))
    css = """
    @page { size: A4; margin: 0; }
    * { box-sizing: border-box; }
    body { margin: 0; color: #172e37; font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.32; }
    .page { width: 210mm; height: 297mm; padding: 13mm 15mm 16mm; position: relative; break-after: page; }
    .page:last-child { break-after: auto; }
    h1 { font-size: 23pt; line-height: 1.15; margin: 0 0 3mm; letter-spacing: -.5pt; }
    h2 { font-size: 15pt; line-height: 1.18; margin: 4mm 0 2.5mm; color: #0f6264; }
    h3 { font-size: 11pt; margin: 3mm 0 2mm; }
    p { margin: 0 0 2.6mm; }
    table { width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; font-size: 9.1pt; line-height: 1.26; }
    th { background: #e4f0ef; color: #125657; text-align: left; font-weight: bold; }
    td, th { padding: 1.7mm 1.8mm; border-bottom: .2mm solid #d9e3e5; vertical-align: top; }
    tr { break-inside: avoid; }
    td:first-child { white-space: nowrap; }
    th:first-child { white-space: nowrap; }
    a { color: #125f86; text-decoration: underline; text-underline-offset: 1pt; }
    strong { color: #142c35; }
    footer { position: absolute; bottom: 8mm; left: 15mm; right: 15mm; border-top: .3mm solid #adc8c8;
             padding-top: 2mm; font-size: 8pt; color: #5a7077; }
    footer span { float: right; }
    @media screen { body { background: #edf1f3; } .page { margin: 10mm auto; background: white;
        box-shadow: 0 1mm 6mm #18333e20; } }
    """
    html = ('<!doctype html><html lang="uk"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Спільний бюджет Олі й Марка — домашнє завдання 7</title>'
            f'<style>{css}</style><body>{sections}</body></html>')
    (ROOT / "product-decision.html").write_text(html, encoding="utf-8")
    print("Built submission/product-decision.html (5 A4 pages)")


if __name__ == "__main__":
    main()

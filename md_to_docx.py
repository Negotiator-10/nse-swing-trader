"""
md_to_docx.py — convert the project's markdown deliverables to Word (.docx).

Project convention: prose deliverables ship as Word documents; only source
files stay as .py. This handles the markdown subset we actually use:
headings (#..####), bullet lists (- / *), pipe tables, **bold**, `code`,
and paragraphs. Not a general markdown engine — just enough for our docs.

Usage:
    python md_to_docx.py docs/phase1_summary.md [more.md ...]
    python md_to_docx.py --all        # convert every .md under the project (except PLAN? see below)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent

# Order matters: **bold** must be tried before *italic*, or the italic branch would
# swallow the first two asterisks of a bold span and emit stray '*' into the document.
# The italic branch requires non-space immediately inside both delimiters, so a bare
# asterisk used as multiplication ("4 * 5") is left alone instead of being italicised.
_INLINE = re.compile(r"(\*\*.+?\*\*|\*(?=\S)[^*\n]*?(?<=\S)\*|`.+?`)")


def _add_runs(paragraph, text: str):
    """Render **bold**, *italic* and `code` inline spans into a paragraph."""
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = paragraph.add_run(part[2:-2]); r.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            r = paragraph.add_run(part[1:-1]); r.italic = True
        elif part.startswith("`") and part.endswith("`"):
            r = paragraph.add_run(part[1:-1])
            r.font.name = "Consolas"; r.font.color.rgb = RGBColor(0xB0, 0x30, 0x60)
        else:
            paragraph.add_run(part)


def _flush_table(doc, rows):
    """rows = list of list[str]; first row is the header."""
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    t = doc.add_table(rows=0, cols=ncols)
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        cells = t.add_row().cells
        for j in range(ncols):
            txt = row[j] if j < len(row) else ""
            cells[j].paragraphs[0].clear()
            _add_runs(cells[j].paragraphs[0], txt)
            if i == 0:
                for run in cells[j].paragraphs[0].runs:
                    run.bold = True
    doc.add_paragraph()


def convert(md_path: Path) -> Path:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    tbl_buf: list[list[str]] = []
    in_code = False

    def flush():
        nonlocal tbl_buf
        if tbl_buf:
            _flush_table(doc, tbl_buf)
            tbl_buf = []

    for raw in lines:
        line = raw.rstrip()

        # ``` fenced blocks -> verbatim monospace (equations, formulas, worked arithmetic).
        # Nothing inside is parsed as markdown, so asterisks and pipes survive intact.
        if line.strip().startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(raw if raw.strip() else " ")
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
            continue

        # ![alt](path) -> embedded picture, scaled to the text width
        m_img = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if m_img:
            flush()
            src = Path(m_img.group(2))
            if not src.is_absolute():
                src = (md_path.parent / src).resolve()
            if src.exists():
                doc.add_picture(str(src), width=Inches(6.0))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if m_img.group(1):
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cr = cap.add_run(m_img.group(1))
                    cr.italic = True
                    cr.font.size = Pt(9)
            else:
                doc.add_paragraph(f"[missing figure: {src}]")
            continue

        # table rows
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):  # separator row
                continue
            tbl_buf.append(cells)
            continue
        flush()

        if not line.strip():
            continue
        if line.startswith("#### "):
            doc.add_heading(line[5:], level=4)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif re.match(r"^\s*[-*] ", line):
            indent = len(line) - len(line.lstrip())
            p = doc.add_paragraph(style="List Bullet 2" if indent >= 2 else "List Bullet")
            _add_runs(p, re.sub(r"^\s*[-*] ", "", line))
        elif re.match(r"^\s*\d+\. ", line):
            p = doc.add_paragraph(style="List Number")
            _add_runs(p, re.sub(r"^\s*\d+\. ", "", line))
        else:
            _add_runs(doc.add_paragraph(), line)
    flush()

    out = md_path.with_suffix(".docx")
    doc.save(out)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--all"]:
        targets = sorted(ROOT.rglob("*.md"))
    else:
        targets = [Path(a) if Path(a).is_absolute() else ROOT / a for a in args]
    for md in targets:
        out = convert(md)
        print(f"  {md.name}  ->  {out.name}")

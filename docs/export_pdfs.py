from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Preformatted, PageBreak

ROOT = Path(__file__).resolve().parent
blue = colors.HexColor("#183B56")
body = ParagraphStyle("Body", fontName="Helvetica", fontSize=10.6, leading=14.2,
                      textColor=colors.HexColor("#202B35"), spaceAfter=9)
heading = ParagraphStyle("Heading", parent=body, fontName="Helvetica-Bold", fontSize=13,
                         leading=17, spaceBefore=12, spaceAfter=7, textColor=blue, keepWithNext=True)
title = ParagraphStyle("Title", parent=heading, fontSize=23, leading=28, spaceAfter=17)
code = ParagraphStyle("Code", parent=body, fontName="Courier", fontSize=9, leading=12)

def footer(canvas, doc):
    canvas.setStrokeColor(colors.HexColor("#CAD6DF"))
    canvas.line(48, 45, A4[0] - 48, 45)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(blue)
    canvas.drawString(48, 31, "SHODH AI / Engineering assessment")
    canvas.drawRightString(A4[0] - 48, 31, str(doc.page))

def inline(line):
    line = escape(line).replace("—", "-").replace("–", "-")
    line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
    return re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', line)

for stem in ("engineering_note", "ai_assistance"):
    blocks = []
    code_lines = None
    for line in (ROOT / f"{stem}.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            if code_lines is None:
                code_lines = []
            else:
                blocks.extend([Preformatted("\n".join(code_lines), code), Spacer(1, 6)])
                code_lines = None
        elif code_lines is not None:
            code_lines.append(line)
        elif line.startswith("# "):
            blocks.append(Paragraph(inline(line[2:]), title))
        elif line.startswith("## "):
            if stem == "engineering_note" and line.startswith(("## 3.", "## 5.")):
                blocks.append(PageBreak())
            blocks.append(Paragraph(inline(line[3:]), heading))
        elif line:
            blocks.append(Paragraph(inline(line), body))
    pdf = SimpleDocTemplate(str(ROOT / f"{stem}.pdf"), pagesize=A4, leftMargin=48,
                            rightMargin=48, topMargin=45, bottomMargin=57,
                            title=stem.replace("_", " ").title())
    pdf.build(blocks, onFirstPage=footer, onLaterPages=footer)

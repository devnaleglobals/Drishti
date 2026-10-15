"""PDF version of the incident report (email attachment and download). Pure Python (reportlab)."""
from __future__ import annotations

import io
import os
import re
from xml.sax.saxutils import escape

import pandas as pd
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import credibility as CR

_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
try:  # Vera ships with reportlab and covers more characters than the built-in fonts
    pdfmetrics.registerFont(TTFont("Vera", os.path.join(_FONT_DIR, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("Vera-Bold", os.path.join(_FONT_DIR, "VeraBd.ttf")))
    F, FB = "Vera", "Vera-Bold"
except Exception:
    F, FB = "Helvetica", "Helvetica-Bold"

INK, MUTED, LINE, BLUE = colors.HexColor("#16202C"), colors.HexColor("#5B6B7F"), colors.HexColor("#D0D7E2"), colors.HexColor("#1F5FAD")
RED, GREEN, HEAD = colors.HexColor("#B42318"), colors.HexColor("#067647"), colors.HexColor("#EEF2F7")

ST = {
    "h1": ParagraphStyle("h1", fontName=FB, fontSize=16, leading=20, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName=F, fontSize=9, leading=12, textColor=MUTED, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName=FB, fontSize=11.5, leading=15, textColor=INK, spaceBefore=12, spaceAfter=5),
    "h3": ParagraphStyle("h3", fontName=FB, fontSize=10, leading=13, textColor=INK, spaceBefore=8, spaceAfter=3),
    "p": ParagraphStyle("p", fontName=F, fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName=F, fontSize=7.5, leading=10, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName=F, fontSize=7.5, leading=9.5, textColor=INK),
    "bullet": ParagraphStyle("bullet", fontName=F, fontSize=9, leading=12.5, textColor=INK, leftIndent=10, bulletIndent=0),
}


def _t(x) -> str:
    return escape(str(x if x is not None else ""))


def _pc(v, mult=1):
    return "–" if v is None else "%+.2f%%" % (v * mult)


def _md_to_flow(text: str) -> list:
    """Very small Markdown subset: ## headings, - bullets, **bold**, paragraphs."""
    out, para = [], []

    def inline(s):
        s = _t(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    def flush():
        if para:
            out.append(Paragraph(inline(" ".join(para)), ST["p"]))
            out.append(Spacer(1, 4))
            para.clear()
    for line in (text or "").splitlines():
        ln = line.strip()
        if not ln:
            flush()
        elif ln.startswith("#"):
            flush()
            out.append(Paragraph(inline(ln.lstrip("#").strip()), ST["h3"]))
        elif ln.startswith(("- ", "* ")):
            flush()
            out.append(Paragraph(inline(ln[2:]), ST["bullet"], bulletText="•"))
        else:
            para.append(ln)
    flush()
    return out


def _table(rows, widths, header=True, zebra=False):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("FONT", (0, 0), (-1, -1), F, 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), HEAD), ("FONT", (0, 0), (-1, 0), FB, 8)]
    t.setStyle(TableStyle(style))
    return t


def build(inc: dict) -> bytes:
    t, p, a = inc["trigger"], inc.get("price", {}), inc.get("assessment") or {}
    when = pd.Timestamp(t["time"]).tz_convert("Asia/Kolkata").strftime("%d %b %Y, %H:%M IST")
    W = A4[0] - 30 * mm
    story = []
    arrow = "up" if t["value"] > 0 else "down"
    story.append(Paragraph(f"{_t(inc['name'])} ({_t(inc['symbol'])}): {arrow} {abs(t['move']):.2f}%", ST["h1"]))
    story.append(Paragraph(f"Material price movement report. Trigger {when}. {_t(inc.get('reason', ''))} "
                           f"Report ready {inc.get('duration_min', '–')} min after the trigger.", ST["sub"]))

    def yn(b):
        return f'<font color="{"#B42318" if b else "#067647"}"><b>{"YES" if b else "NO"}</b></font>'
    verdict = [
        [Paragraph("Material movement", ST["p"]), Paragraph(yn(a.get("material")) + "&nbsp;&nbsp;" + _t(a.get("material_reason", "")), ST["p"])],
        [Paragraph("Potential rumour", ST["p"]), Paragraph(yn(a.get("rumour")) + (" (mainstream media)" if a.get("rumour_in_mainstream") else ""), ST["p"])],
        [Paragraph("Assessment", ST["p"]), Paragraph(f"<b>{_t(a.get('category', ''))}</b>", ST["p"])],
        [Paragraph("Likely driver", ST["p"]), Paragraph(_t(a.get("driver") or "Not identified"), ST["p"])],
        [Paragraph("Suggested action", ST["p"]), Paragraph(_t(a.get("action", "")), ST["p"])],
    ]
    vt = Table(verdict, colWidths=[38 * mm, W - 38 * mm])
    vt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
                            ("BACKGROUND", (0, 0), (0, -1), HEAD), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(vt)

    story.append(Paragraph("Price and index", ST["h2"]))
    pr = [["Move vs previous close", _pc(t.get("move")), f"{t.get('benchmark', 'Index')} move", _pc(t.get("index_move"))],
          ["Index-adjusted move", _pc(t.get("adjusted")), p.get("split_label", "Pre-9:30") + " move", _pc(p.get("pre_930"), 100)],
          ["Move after that", _pc(p.get("post_930"), 100), "Move started (exchange time)", p.get("onset_local") or "–"],
          ["Volume vs 20-day average", ("%.1fx" % p["volume_ratio"]) if p.get("volume_ratio") else "–", "Triggers",
           f"+{t.get('up_pct', t['threshold']):g}% / -{t.get('down_pct', t['threshold']):g}%"]]
    story.append(_table(pr, [48 * mm, 30 * mm, 52 * mm, W - 130 * mm], header=False))

    story.append(Paragraph("Potential rumours and market information (ranked)", ST["h2"]))
    ev = {e["id"]: e for e in inc.get("evidence", [])}
    if not inc.get("clusters"):
        story.append(Paragraph("No relevant public information found in the trigger window.", ST["p"]))
    for n, c in enumerate(inc.get("clusters", [])[:8], 1):
        block = [Paragraph(f"{n}. {_t(c['label'])}", ST["h3"]),
                 Paragraph(f"Score {c['score']:.2f} | {_t(c['type'].replace('_', ' '))} | {_t(c['direction'])} | first seen "
                           f"{_t(c['first_time'])} via {_t(c['first_source'])} ({'before' if c['before_onset'] else 'after'} the move started) | "
                           f"{c['outlets']} outlet(s) | {_t(', '.join(CR.TIERS[x]['label'] for x in c['tiers']))}", ST["small"])]
        if c.get("summary"):
            block.append(Paragraph(_t(c["summary"]), ST["p"]))
        for i in c["items"][:4]:
            e = ev.get(i)
            if e:
                block.append(Paragraph(f'<a href="{_t(e["url"] or "")}" color="#1F5FAD">{_t(e["title"][:160])}</a> '
                                       f'<font color="#5B6B7F">{_t(e["source"])}, {_t(e["time"])}</font>', ST["bullet"], bulletText="•"))
        story.append(KeepTogether(block))

    story.append(Paragraph("Analyst report", ST["h2"]))
    story += _md_to_flow(inc.get("report_md", ""))

    story.append(Paragraph("Evidence trail", ST["h2"]))
    rows = [["Published", "Min vs start", "Source", "Credibility", "Type", "Dir.", "Conf.", "Item"]]
    for e in sorted(inc.get("evidence", []), key=lambda x: abs(x["minutes_vs_onset"]))[:80]:
        rows.append([Paragraph(_t(e["time"]), ST["cell"]), str(e["minutes_vs_onset"]), Paragraph(_t(e["source"]), ST["cell"]),
                     Paragraph(_t(CR.TIERS[e["tier"]]["label"]), ST["cell"]), Paragraph(_t(e.get("type", "").replace("_", " ")), ST["cell"]),
                     _t(e.get("direction", ""))[:3], "%.2f" % e.get("confidence", 0),
                     Paragraph(f'<a href="{_t(e["url"] or "")}" color="#1F5FAD">{_t(e["title"][:140])}</a>', ST["cell"])])
    story.append(_table(rows, [22 * mm, 13 * mm, 24 * mm, 20 * mm, 20 * mm, 9 * mm, 10 * mm, W - 118 * mm]))
    if len(inc.get("evidence", [])) > 80:
        story.append(Paragraph(f"Showing the 80 items closest to the start of the move, of {len(inc['evidence'])}. "
                               "The full trail is in Drishti.", ST["small"]))

    story.append(Paragraph("Method and limitations", ST["h2"]))
    story.append(Paragraph(
        "Trigger: price vs previous close and index-adjusted move against the configured high and low triggers. Investigation: public "
        "news, exchange and regulator filings, forums, video and social sources around the start of the move; items are timed against "
        "that start, rated for source credibility, classified and clustered by AI, and ranked. Supports the material price movement and "
        "market-rumour verification process (SEBI LODR Regulation 30(11), NSE/SURV/62122 as configured); the final assessment rests "
        "with the surveillance team. Free data sources can be delayed or incomplete. Not investment advice.", ST["small"]))

    buf = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(F, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(15 * mm, 9 * mm, f"Drishti surveillance | {inc['symbol']} | {when} | incident {inc['id']}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{inc['symbol']} movement report", author="Drishti")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()

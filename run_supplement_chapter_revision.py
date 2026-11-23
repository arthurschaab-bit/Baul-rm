"""Replace only the two requested chapters of an existing local PDF."""
import argparse
from datetime import date
from io import BytesIO
import json
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape

import fitz
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

INTRO_3 = ("Die Bauarbeiten beeinträchtigen die Nutzung der Wohnung über die gemessenen Pegel hinaus. "
           "Das Bautagebuch beschreibt insbesondere:")
INTRO_8 = ("Die Übersicht verknüpft datierte Mitteilungen des Bauträgers mit eigenen datierten "
           "Belegen und Auszügen aus dem Bautagebuch.")
TITLE_3 = "3 Auswirkungen auf die Wohnnutzung"
TITLE_8 = "8 Bauarbeiten vor Beginn der Pegelmessung"
FORBIDDEN = (
    "nicht Gegenstand der Minderungsquoten", "gesondert geltend gemacht", "Phase 1", "Phase 2",
    "Phase 3", "zulässiger Bauzeit", "TA Lärm", "Richtwert Wohngebiet", "Mandant", "K2",
    "Briefing", "intern", "ADHS", "Allergie", "[DATUM]", "[AKTENZEICHEN]", "bitte ergänzen",
)


def flat(text):
    return " ".join((text or "").split())


def table_rows(page, header):
    for table in page.find_tables().tables:
        rows = [[flat(cell) for cell in row] for row in table.extract()]
        if rows and rows[0][0] == header:
            return rows
    raise ValueError(f"Missing base table {header!r} on page {page.number + 1}")


def find_chapter(pdf, title):
    found = [p.number for p in pdf if title in flat(p.get_text())]
    if len(found) != 1:
        raise ValueError(f"Expected one page for {title}, found {found}")
    return found[0]


def fonts():
    folder = (Path(os.environ["SystemRoot"]) / "Fonts" if os.name == "nt"
              else Path("/usr/share/fonts/truetype/dejavu"))
    for regular, bold in (("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"), ("arial.ttf", "arialbd.ttf")):
        if (folder / regular).is_file() and (folder / bold).is_file():
            pdfmetrics.registerFont(TTFont("Base", str(folder / regular)))
            pdfmetrics.registerFont(TTFont("Base-Bold", str(folder / bold)))
            return
    raise ValueError("No supported font pair found")


def paragraph(text, size=9, bold=False):
    style = ParagraphStyle("body", fontName="Base-Bold" if bold else "Base",
                           fontSize=size, leading=size * 1.32, textColor=colors.HexColor("#172d38"))
    return Paragraph(escape(text), style)


def draw_paragraph(canvas, text, y, width, size=9, bold=False):
    item = paragraph(text, size, bold)
    _, height = item.wrap(width, 1000)
    item.drawOn(canvas, 39, y - height)
    return y - height - 9


def draw_table(canvas, rows, y, widths, size=7.1, padding=3):
    vals = [[paragraph(v, size, i == 0) for v in row] for i, row in enumerate(rows)]
    table = Table(vals, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f1f1")),
        ("GRID", (0, 0), (-1, -1), .25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    _, height = table.wrap(sum(widths), 1000)
    if y - height < 48:
        raise ValueError("Chapter table does not fit above the preserved footer")
    table.drawOn(canvas, 45, y - height)
    return y - height - 12


def iso_week(text):
    match = re.fullmatch(r"KW\s+(\d{2})/(\d{4})", text)
    if not match:
        raise ValueError(f"Unexpected base week label: {text!r}")
    return int(match[2]), int(match[1])


def validated_excerpts(entries, start, end, banned):
    chosen = {}
    for entry in entries:
        day = date.fromisoformat(entry["date"])
        week = day.isocalendar()[:2]
        excerpt = entry["excerpt"]
        original = flat(entry["original"])
        source_text = excerpt.removesuffix(" …").removeprefix("… ")
        display = day.strftime("%d.%m.: ") + excerpt
        if (
            not start <= day <= end or week in chosen or len(display) > 160
            or not source_text or source_text not in original
            or any(token.casefold() in display.casefold() for token in banned)
        ):
            raise ValueError(f"Invalid excerpt at source row {entry['row']}")
        if source_text != original and "…" not in excerpt:
            raise ValueError(f"Shortened excerpt must carry an ellipsis at row {entry['row']}")
        chosen[week] = display
    return chosen


def replace_chapters(source, output, profile):
    if output.exists():
        raise FileExistsError(output)
    candidate = output.with_suffix('.candidate.pdf')
    fonts()
    impacts = profile["impacts"]
    if len(impacts) != 13:
        raise ValueError("Expected exactly 13 supplied descriptions")
    banned = FORBIDDEN + tuple(profile.get("additional_forbidden", []))
    excerpts = validated_excerpts(profile["excerpts"], date.fromisoformat(profile["start"]),
                                  date.fromisoformat(profile["end"]), banned)
    with fitz.open(source) as doc:
        p3, p8 = (find_chapter(doc, title) for title in (TITLE_3, TITLE_8))
        old_early = table_rows(doc[p3], "Datum")
        early_title = next(flat(x) for x in doc[p3].get_text().splitlines() if x.startswith("Arbeitsbeginn vor 7 Uhr"))
        base = table_rows(doc[p8], "Kalenderwoche")
        weeks = {iso_week(x[0]): x for x in base[1:]}
        report_rows = [
            weeks.get(key, [f"KW {key[1]:02}/{key[0]}", "", ""]) + [excerpts.get(key, "")]
            for key in sorted(weeks.keys() | excerpts.keys())
        ]
        contents = BytesIO()
        w, h = doc[p3].rect.width, doc[p3].rect.height
        canvas = Canvas(contents, pagesize=(w, h), pageCompression=1)
        y = draw_paragraph(canvas, TITLE_3, h - 40, w - 78, 17, True)
        y = draw_paragraph(canvas, INTRO_3, y, w - 78)
        y = draw_table(canvas, [["Beeinträchtigung"], *[[x] for x in impacts]], y, [w - 78])
        y = draw_paragraph(canvas, early_title, y, w - 78, 11, True)
        draw_table(canvas, old_early, y, [92, 68, w - 238])
        canvas.showPage()
        y = draw_paragraph(canvas, TITLE_8, h - 40, w - 78, 17, True)
        y = draw_paragraph(canvas, INTRO_8, y, w - 78)
        draw_table(canvas, [base[0] + ["Auszug Bautagebuch"], *report_rows], y, [70, 148, 120, w - 416],
                   size=7.1, padding=2.5)
        canvas.showPage()
        canvas.save()
        with fitz.open(stream=contents.getvalue(), filetype="pdf") as overlay:
            for target, overlay_page in ((p3, 0), (p8, 1)):
                page = doc[target]
                page.add_redact_annot(fitz.Rect(0, 30, w, h - 42), fill=(1, 1, 1))
                page.apply_redactions()
                page.show_pdf_page(page.rect, overlay, overlay_page)
        doc.save(candidate, garbage=4, deflate=True, use_objstms=1)
    with fitz.open(source) as old, fitz.open(candidate) as new:
        if len(old) != len(new):
            raise ValueError("Unexpected page count change")
        for i, (a, b) in enumerate(zip(old, new)):
            if i not in (p3, p8):
                if a.get_text() != b.get_text() or a.get_pixmap().samples != b.get_pixmap().samples:
                    raise ValueError(f"Unchanged page {i+1} differs")
            elif a.get_pixmap(clip=fitz.Rect(0, h-41, w, h)).samples != b.get_pixmap(clip=fitz.Rect(0, h-41, w, h)).samples:
                raise ValueError(f"Footer differs on page {i+1}")
        chapter = flat(new[p3].get_text())
        rendered_impacts = chapter.split("Beeinträchtigung ", 1)[1].split(early_title, 1)[0].strip()
        if rendered_impacts != flat(" ".join(impacts)) or "Beispiele (Datum)" in chapter:
            raise ValueError("Impact rows do not match")
        if table_rows(new[p3], "Datum") != old_early:
            raise ValueError("Early-start table changed")
        if table_rows(new[p8], "Kalenderwoche") != [base[0] + ["Auszug Bautagebuch"], *report_rows]:
            raise ValueError("Rendered weekly table does not match sources")
        full_text = flat(" ".join(p.get_text() for p in new))
        hits = [term for term in banned if term.casefold() in full_text.casefold()]
        if hits:
            raise ValueError(f"Forbidden expressions in output: {hits}")
        for ref in set(re.findall(r"Kapitel\s+(\d+)", full_text)):
            if not any(re.search(rf"(?m)^{ref}\s+\D", p.get_text()) for p in new):
                raise ValueError(f"Missing chapter reference {ref}")
        page_count = len(new)
    candidate.rename(output)
    return {"datei": output.name, "seiten": page_count, "ersetzte_seiten": [p3+1,p8+1],
            "auszuege": len(excerpts), "wochenzeilen": len(report_rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    result = replace_chapters(args.source, args.output, json.loads(args.profile.read_text(encoding="utf-8-sig")))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

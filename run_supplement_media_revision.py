"""Revise v5 wording and add locally verified media to the report."""

import argparse
from datetime import date
from io import BytesIO
import json
from pathlib import Path

import fitz
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from run_supplement_chapter_revision import draw_paragraph, draw_table, flat, fonts, paragraph
from run_supplement_threshold_revision import overlay, rect_from_hits, redact
from run_supplement_wording_revision import PLAN, impact_rows


SECTION = "Ergänzende Bild- und Videobelege"
LOCATION = (
    "Lageplan: Küche, Flur, Schlafzimmer und Nordwestbalkon grenzen unmittelbar an die Baustelle "
    "(gemeinsame Brandwand, ca. 40 cm). Der kürzeste waagrechte Abstand vom Südostbalkon zur "
    "Baustelle beträgt rund 4 m."
)
STREET = (
    "Straßenseite: Gerüst mit Schutzplane vor dem Nordwestbalkon, zurückgebaut im April 2026."
)
COURTYARD = (
    "Hofseite: Gerüst mit Staubschutzvorhang vor Schlafzimmer und Südostbalkon, aufgebaut am "
    "29.01.2026, durchgehend bis Oktober 2026. Das Gerüst beeinträchtigte Sicht und Belichtung."
)
NOTE = (
    "Momentanwerte aus Videoaufnahmen des Messgeräts. Sie sind in der Tageszählung nach "
    "Kapitel 10 nicht enthalten und werden getrennt ausgewiesen."
)
VIDEO_TITLE = "Videoaufnahmen des Messgeräts vor Beginn der Dauermessung am 01.06.2026"
LEGACY_DATE = "06.07.2026"
DATE_TEXT = "6. Juli 2026"


def patch_impacts(page):
    header = rect_from_hits(page, "Beeinträchtigung")
    early = rect_from_hits(page, "Arbeitsbeginn vor 7 Uhr")
    box = fitz.Rect(44, header.y0 - 6, page.rect.width - 31, early.y0 - 3)
    rows = impact_rows(page, box)
    if len(rows) != 15 or not rows[-1].startswith("Gerüst mit Schutzplane am Mietobjekt"):
        raise ValueError("Expected the v5 impact table with 14 entries")
    rows[8] += f" (siehe {SECTION})"
    rows[9] += f" (siehe {SECTION})"
    rows[11] = rows[11].replace("(Fotos: Kapitel 8)", f"(siehe {SECTION})")
    rows[-2] = STREET.removesuffix(".") + f" (siehe {SECTION})."
    rows[-1] = COURTYARD.removesuffix(".") + f" (siehe {SECTION})."
    redact(page, box, graphics=True)

    def draw(canvas):
        remaining = draw_table(
            canvas, [[r] for r in rows], page.rect.height - header.y0 + 5,
            [page.rect.width - 78], padding=2.0,
        )
        if remaining + 12 < page.rect.height - box.y1:
            raise ValueError("Impact table overlaps the early-start section")

    overlay(page, draw)
    return box, rows


def patch_map(page):
    old_text = rect_from_hits(page, PLAN)
    redact(page, old_text + (-0.4, -0.4, 0.4, 0.4))
    height = page.rect.height

    def label(canvas, text, x, top, w, fill, color):
        canvas.setFillColor(colors.HexColor(fill))
        canvas.rect(x, height - top - 17, w, 17, fill=1, stroke=0)
        canvas.setFont("Base-Bold", 5.5)
        canvas.setFillColor(colors.HexColor(color))
        for index, line in enumerate(text):
            canvas.drawString(x + 3, height - top - 6.8 - 6.5 * index, line)

    def draw(canvas):
        draw_paragraph(canvas, LOCATION, height - 68, page.rect.width - 78, 8.2)
        label(canvas, ["Südostbalkon:", "< 4 m zur Baustelle"], 298, 211.5, 68, "#ffffff", "#9b251e")
        canvas.setStrokeColor(colors.HexColor("#124951"))
        canvas.setLineWidth(1.0)
        canvas.line(228, height - 223, 255, height - 225)
        label(canvas, ["Nordwestbalkon:", "Wand an Wand (Brandwand ca. 40 cm)"], 139, 211.5, 104, "#ffffff", "#124951")

    overlay(page, draw)


def patch_date(page):
    hits = page.search_for(LEGACY_DATE)
    if len(hits) != 1:
        raise ValueError("Expected one unchanged measurement on 6 July")
    box = hits[0]
    redact(page, box)
    overlay(page, lambda c: draw_paragraph_at(c, DATE_TEXT, box.x0, page.rect.height - box.y1 + 2, 7.1))


def draw_paragraph_at(canvas, text, x, baseline, size):
    canvas.setFillColor(colors.HexColor("#172d38"))
    canvas.setFont("Base", size)
    canvas.drawString(x, baseline, text)


def patch_chapter_eight(page):
    # The existing table ends above the private report footer.
    top = max(b[3] for b in page.get_text("blocks") if b[1] < 780) + 11
    if top > 785:
        raise ValueError("No room below the chapter 8 table")
    overlay(page, lambda c: draw_paragraph(
        c, f"Bild-, Video- und Schriftbelege: siehe „{SECTION}“.",
        page.rect.height - top, page.rect.width - 78, 8.2,
    ))


def compressed_image(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((700, 440))
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=72, optimize=True)
    return ImageReader(stream)


def evidence_pdf(rect, profile):
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(rect.width, rect.height), pageCompression=1)
    counter = 0

    def header(title):
        nonlocal counter
        counter += 1
        draw_paragraph(canvas, title, rect.height - 42, rect.width - 78, 16, True)
        draw_paragraph_at(canvas, profile["footer"], 39, 19, 7)
        folio = f"10{chr(96 + counter)}" if counter <= 26 else f"10-{counter}"
        draw_paragraph_at(canvas, folio, rect.width - 53, 19, 7)
        return rect.height - 78

    headers = ["Datum", "Uhrzeit", "Ort", "Befund", "Dateiname"]
    rows = profile["rows"]
    photos = [row for row in [*rows, *profile["videos"]] if row.get("image")]
    if len(photos) > 6:
        raise ValueError("More than six figures requested")
    videos = profile["videos"]
    for index in range(0, len(videos), 16):
        top = header(SECTION if index == 0 else f"{SECTION} (Fortsetzung)")
        if index == 0:
            top = draw_paragraph(canvas, VIDEO_TITLE, top, rect.width - 78, 10, True)
            top = draw_paragraph(canvas, NOTE, top, rect.width - 78, 8.3)
            top = draw_paragraph(
                canvas, "Anzeigebereich aus den lesbaren Standbildern im Fünf-Sekunden-Raster.",
                top, rect.width - 78, 8.3,
            )
        keys = ("date", "time", "where", "window", "levels", "name")
        subset = [[row[key] for key in keys] for row in videos[index:index + 16]]
        video_headers = ["Datum", "Uhrzeit", "Ort", "Fenster/Tür", "Min.–Max. dB(A)", "Datei"]
        draw_table(canvas, [video_headers, *subset], top, [55, 43, 76, 75, 65, 203], size=7.2, padding=4)
        canvas.showPage()

    top = header(f"{SECTION} (Zusammenfassung)")
    top = draw_paragraph(
        canvas, "Höchste abgelesene Anzeigewerte je Tag und Messort", top,
        rect.width - 78, 10, True,
    )
    top = draw_table(
        canvas, [["Datum", "Ort", "Höchster Anzeigewert dB(A)"], *profile["summary"]],
        top, [85, 238, 194], size=8, padding=4,
    )
    top = draw_paragraph(canvas, profile["outdoor"], top, rect.width - 78, 9)
    top = draw_paragraph(canvas, "Schlafzimmer innen", top, rect.width - 78, 10, True)
    draw_paragraph(canvas, profile["comparison"], top, rect.width - 78, 9)
    canvas.showPage()

    for index in range(0, len(rows), 13):
        top = header(f"{SECTION} (Baustellenbelege)")
        subset = [[row[key] for key in ("date", "time", "where", "finding", "name")] for row in rows[index:index+13]]
        draw_table(canvas, [headers, *subset], top, [61, 42, 88, 185, 141], size=7.35, padding=5)
        canvas.showPage()

    if photos:
        top = header(f"{SECTION} (Abbildungen)")
        for index, row in enumerate(photos):
            item = compressed_image(row["image"])
            iw, ih = item.getSize()
            width, height = iw * min(242 / iw, 175 / ih), ih * min(242 / iw, 175 / ih)
            x = 45 + index % 2 * 253
            y = top - index // 2 * 225
            canvas.drawImage(item, x + (242 - width) / 2, y - height, width, height)
            caption = paragraph(f"{row['date']} · {row['finding']}", 7.3)
            _, h = caption.wrap(242, 40)
            caption.drawOn(canvas, x, y - height - h - 5)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


def verify(original, final, impact_data, evidence_pages, chapter_eight):
    changes = {2, 5, 9}
    if chapter_eight:
        changes.add(8)
    for index, page in enumerate(original):
        if index in changes:
            continue
        new = final[index + evidence_pages if index >= 11 else index]
        if new.get_text() != page.get_text() or new.get_pixmap().samples != page.get_pixmap().samples:
            raise ValueError(f"Unrequested change on source page {index + 1}")
    text = flat(" ".join(page.get_text() for page in final))
    if any(term in text for term in ("Brandmauer", "00:00", LEGACY_DATE)) or "Kinderzimmer" in flat(final[5].get_text()):
        raise ValueError("A forbidden expression persists")
    if text.count("Küche, Flur, Schlafzimmer und Nordwestbalkon") != 1:
        raise ValueError("Missing or duplicated location text")
    if impact_rows(final[2], impact_data[0]) != impact_data[1]:
        raise ValueError("Unexpected impact wording or layout")
    def restore_date(page):
        return sorted(flat(line) for line in page.get_text().replace(DATE_TEXT, LEGACY_DATE).splitlines() if line.strip())
    if restore_date(original[9]) != restore_date(final[9]):
        raise ValueError("A measurement table entry changed")


def revise(source, output, profile):
    if output.exists():
        raise FileExistsError(output)
    fonts()
    cutoff = date.fromisoformat(profile["cutoff"])
    for row in [*profile["rows"], *profile["videos"]]:
        media_day = date(int(row["date"][6:]), int(row["date"][3:5]), int(row["date"][:2]))
        if media_day > cutoff:
            if cutoff != date(2026, 9, 30) or media_day not in {date(2026, 10, 6), date(2026, 10, 14)}:
                raise ValueError("An evidence date exceeds the cutoff")
    with fitz.open(source) as original, fitz.open(source) as doc:
        impacts = patch_impacts(doc[2])
        patch_map(doc[5])
        if profile["chapter_eight"]:
            patch_chapter_eight(doc[8])
        patch_date(doc[9])
        with fitz.open(stream=evidence_pdf(doc[0].rect, profile), filetype="pdf") as annex:
            evidence_pages = len(annex)
            doc.insert_pdf(annex, start_at=11)
        payload = doc.tobytes(garbage=4, deflate=True)
        with fitz.open(stream=payload, filetype="pdf") as final:
            verify(original, final, impacts, evidence_pages, profile["chapter_eight"])
        if cutoff == date(2026, 9, 30) and len(payload) > 8_000_000:
            raise ValueError("Variant A exceeds 8 MB")
    with output.open("xb") as handle:
        handle.write(payload)
    print(f"{output.name}: {12 + evidence_pages} pages, {len(payload)} bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
    revise(args.source, args.output, profile)


if __name__ == "__main__":
    main()

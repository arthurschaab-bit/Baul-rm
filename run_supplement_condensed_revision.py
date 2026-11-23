"""Condense the v6 report using local, reviewed media and unchanged day metrics."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import re

import fitz
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from run_supplement_chapter_revision import draw_paragraph, draw_table, flat, fonts, paragraph, table_rows
from run_supplement_media_revision import NOTE, SECTION, compressed_image, draw_paragraph_at
from run_supplement_threshold_revision import overlay


PLACE = (
    "Nordwestbalkon, Küche, Flur und Schlafzimmer grenzen entlang der Abrisskante unmittelbar "
    "an die Baustelle (gemeinsame Brandwand, ca. 40 cm). Unmittelbar angrenzend stand das "
    "fünfgeschossige Gebäude (Abriss 1), dahinter ein eingeschossiger Bau (Abriss 2). Der "
    "kürzeste waagrechte Abstand vom Südostbalkon zur Baustelle beträgt rund 4 m. Die zweite "
    "Abbildung zeigt die Bohrpositionen des Tiefbaus entlang der Grundstücksgrenze, auch direkt "
    "an der Abrisskante."
)
MAP_CAPTION = "Luftbild: Google (genordet, Maßstab oben rechts: 5 m). Eintragungen des Mieters."
EARLY = "Arbeitsbeginn vor 7 Uhr (Nachtzeit nach AVV Baulärm), siehe Kapitel 8."
LIST_NOTE = "Die Einzelliste aller ausgewerteten Videoaufnahmen mit Dateinamen liegt vor und wird auf Anforderung übermittelt."
SUMMARY = "Höchste abgelesene Anzeigewerte je Tag und Messort"


def clear(page, top, bottom, images=False):
    page.add_redact_annot(fitz.Rect(36, top, page.rect.width - 32, bottom), fill=False)
    page.apply_redactions(images=1 if images else 0, graphics=2, text=0)


def text_block(page, starts):
    return next(value for b in page.get_text("blocks") if (value := flat(b[4])).startswith(starts))


def core(page):
    court = text_block(page, "Die Zählung der Tage")
    blocks = [(b[1], flat(b[4])) for b in page.get_text("blocks")]
    heading = [(y, t) for y, t in blocks if 75 < y < 234]
    cards = [t for _, t in heading[:8]]
    cards[1] = cards[1].split(";", 1)[0]
    cards[3] = cards[3].split(";", 1)[0]
    contrast = text_block(page, "Typischer Bautag (")
    median, rest = re.search(r": (\d+,\d+ dB\(A\))\.(.*)", contrast).groups()
    quiet = re.search(r"höchstens (\d+,\d+ dB\(A\))", rest)[1]
    gap = re.search(r"mindestens (\d+,\d+ dB)", rest)[1]
    guides = text_block(page, "Vergleichswerte der AVV")
    clear(page, 70, 790)

    def draw(c):
        y = page.rect.height - 73
        for i in range(0, len(cards), 4):
            for col in range(2):
                x = 39 + col * 262
                c.setFillColor(colors.HexColor("#e9f3f3"))
                c.roundRect(x, y - 53, 252, 53, 5, fill=1, stroke=0)
                draw_paragraph_at(c, cards[i+col*2], x+9, y-22, 14)
                draw_paragraph_at(c, cards[i+col*2+1], x+9, y-39, 8.5)
            y -= 62
        y = draw_paragraph(c, f"Typischer Bautag: {median}; Ruhetag höchstens {quiet}; Abstand mindestens {gap}.", y-5, page.rect.width-78, 10, True)
        y = draw_paragraph(c, court, y, page.rect.width-78, 8.6)
        y = draw_paragraph(c, guides, y, page.rect.width-78, 8.6)
        draw_paragraph(c, "Einzelwerte und alle Messtage: Kapitel 9 und 10.", y, page.rect.width-78, 8.6)

    overlay(page, draw)
    return court


def impacts(page, references):
    region = fitz.Rect(44, 101, page.rect.width - 30, 339)
    rows = [flat(b[4]) for b in page.get_text("blocks", clip=region, sort=True)]
    if len(rows) != 15:
        raise ValueError("Unexpected v6 impact table")
    for index, old in enumerate(rows):
        for prefix, new in references.items():
            if old.startswith(prefix):
                rows[index] = new
                break
    clear(page, 100, 700)

    def draw(c):
        y = draw_table(c, [[row] for row in rows], page.rect.height-100, [page.rect.width-78], padding=2.0)
        draw_paragraph(c, EARLY, y-3, page.rect.width-78, 9.5, True)

    overlay(page, draw)
    return rows


def panel_image(path, crop=None):
    with Image.open(path) as original:
        im = original.convert("RGB")
        if crop:
            im = im.crop(crop)
        buffer = BytesIO()
        im.save(buffer, format="JPEG", quality=94, optimize=True, subsampling=0)
    return ImageReader(buffer)


def media(page, continuation, profile):
    pics = []
    for p, ignore in ((page, 1), (continuation, 0)):
        for item in p.get_image_info(xrefs=True)[ignore:]:
            pics.append(p.parent.extract_image(item["xref"])["image"])
    captions = profile["gallery_captions"]
    if len(pics) != len(captions):
        raise ValueError("Unexpected v6 photos")
    photos = [(compressed_image(BytesIO(blob)), caption) for blob, caption in zip(pics, captions)]
    photos.append((compressed_image(profile["indoor_image"]), "18.04.2026, 09:47 Uhr · Staub am geöffneten Fenster und auf der inneren Fensterbank."))
    clear(page, 68, 790, images=True)
    clear(continuation, 78, 793, images=True)

    def draw_maps(c):
        y = draw_paragraph(c, PLACE, page.rect.height-73, page.rect.width-78, 9)
        for path in profile["maps"]:
            item = panel_image(path, (204, 4, 1397, 894))
            h = 430 * item.getSize()[1] / item.getSize()[0]
            c.drawImage(item, 39, y-h, 430, h)
            inset = panel_image(path, (1258, 17, 1387, 57))
            c.drawImage(inset, 485, y-31, 76, 24)
            y -= h+8
        if y < 44:
            raise ValueError("Maps exceed page")
        draw_paragraph(c, MAP_CAPTION, y, page.rect.width-78, 8)

    def draw_photos(c):
        for i, (item, caption) in enumerate(photos):
            iw, ih = item.getSize()
            scale = min(163/iw, 149/ih)
            w, h = iw*scale, ih*scale
            x = 45 + (i%3)*173
            y = continuation.rect.height - 82 - (i//3)*174
            c.drawImage(item, x+(163-w)/2, y-h, w, h)
            txt = paragraph(caption, 7.2)
            _, th = txt.wrap(163, 40)
            txt.drawOn(c, x, y-h-th-3)

    overlay(page, draw_maps)
    overlay(continuation, draw_photos)


def chapter_eight(page, profile):
    rows = table_rows(page, "Kalenderwoche")
    cases = {r[0]:r for r in rows[1:]}
    cases["KW 47/2025"][1] = profile["notice"]
    cases["KW 12/2026"][3] = "19.03.: 06:45 Uhr, Schlagbohrer und Bagger. " + cases["KW 12/2026"][3]
    cases["KW 13/2026"][3] = "23.03.: 06:45 Uhr Arbeitsbeginn. " + cases["KW 13/2026"][3].removeprefix("23.03.: ")
    cases["KW 16/2026"][3] = "13.04.: 06:30 Uhr, zwei Bagger. " + cases["KW 16/2026"][3]
    rows.insert(1, ["KW 39/2025", "", profile["inspection"], ""])
    if profile.get("later_start"):
        rows.append(["KW 43/2026", "", "", profile["later_start"]])
    clear(page, 96, 790)

    def draw(c):
        y = draw_table(c, rows, page.rect.height-98, [60, 137, 114, 206], size=6.5, padding=1.6)
        draw_paragraph(c, f"Bild- und Videobelege: siehe „{SECTION}“.", y, page.rect.width-78, 8)

    overlay(page, draw)


def replace_sentence(page, prefix, new):
    block = next(b for b in page.get_text("blocks") if flat(b[4]).startswith(prefix))
    page.add_redact_annot(fitz.Rect(block[:4]) + (-.4,-.4,.4,.4), fill=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    overlay(page, lambda c: draw_paragraph(c, new, page.rect.height-block[1]+2, page.rect.width-88, 9))


def annex(rect, profile, evidence):
    buf = BytesIO()
    c = Canvas(buf, pagesize=(rect.width, rect.height), pageCompression=1)

    def heading(part, suffix):
        draw_paragraph(c, SECTION+suffix, rect.height-42, rect.width-78, 16, True)
        draw_paragraph_at(c, profile["footer"], 39, 19, 7)
        draw_paragraph_at(c, part, rect.width-52, 19, 7)
        return rect.height-78

    y = heading("10a", "")
    y = draw_paragraph(c, SUMMARY, y, rect.width-78, 10, True)
    y = draw_table(c, [["Datum","Ort","Höchster Anzeigewert dB(A)"], *profile["summary"]], y, [85,238,194], size=8,padding=3.4)
    for line in (LIST_NOTE, profile["outdoor"], NOTE):
        y = draw_paragraph(c, line, y, rect.width-78, 9)
    y = draw_paragraph(c, "Schlafzimmer innen", y, rect.width-78, 10, True)
    draw_paragraph(c, profile["comparison"], y, rect.width-78, 9)
    c.showPage()

    y = heading("10b", " (Baustellenbelege)")
    keys = ("date","time","where","finding","name")
    draw_table(c, [["Datum","Uhrzeit","Ort","Befund","Dateiname"], *[[r[k] for k in keys] for r in evidence]], y, [61,42,85,187,142], size=7.25, padding=4.5)
    c.showPage()

    y = heading("10c", " (Abbildungen)")
    for i, row in enumerate(profile["figures"]):
        item = compressed_image(row["image"])
        iw,ih=item.getSize()
        scale=min(242/iw,175/ih)
        w,h=iw*scale,ih*scale
        x=45+i%2*253
        top=y-i//2*225
        c.drawImage(item,x+(242-w)/2,top-h,w,h)
        caption=paragraph(f"{row['date']} · {row['finding']}",7.3)
        _,height=caption.wrap(242,40)
        caption.drawOn(c,x,top-h-height-5)
    c.showPage()
    c.save()
    return buf.getvalue()


def revise(source, output, profile):
    fonts()
    with fitz.open(source) as doc, fitz.open(source) as old:
        court = core(doc[1])
        impacts(doc[2], profile["references"])
        media(doc[5],doc[6],profile)
        chapter_eight(doc[8],profile)
        method = text_block(old[21], "Für den Ruhetagswert")
        if "51,3" in method:
            replace_sentence(doc[21], "Für den Ruhetagswert", method.replace("von höchstens 51,3 dB(A) ", ""))
        figures = [*profile["rows"], *profile["videos"]]
        profile["figures"] = [r for r in figures if r.get("image") and r["name"] != profile["old_quiet"]]
        profile["figures"].append(profile["quiet"])
        evidence = [r for r in profile["rows"] if r["name"] not in profile["documents"]]
        for row in evidence:
            row["finding"] = row["finding"].replace("neben der geschützten Wand","neben der Brandwand")
        evidence.extend(profile["extra_evidence"])
        evidence.sort(key=lambda r: tuple(reversed(r["date"].split("."))))
        doc.delete_pages(11,20)
        with fitz.open(stream=annex(doc[0].rect,profile,evidence),filetype="pdf") as pages:
            doc.insert_pdf(pages,start_at=11)
        data=doc.tobytes(garbage=4,deflate=True)
        with fitz.open(stream=data,filetype="pdf") as final:
            all_text=flat(" ".join(p.get_text() for p in final))
            if any(v in all_text for v in ("keine Geräteanzeige","00:00","06.07.2026","geschützten Wand","47-fach")):
                raise ValueError("Forbidden legacy wording")
            if PLACE not in flat(final[5].get_text()) or court not in flat(final[1].get_text()):
                raise ValueError("Plan text or court paragraph changed")
            if "25,7 dB(A)" not in flat(final[11].get_text()) or len(final) != 15:
                raise ValueError("Comparison or page count incorrect")
            for j in (0,3,4,7,9,10):
                if old[j].get_pixmap().samples != final[j].get_pixmap().samples:
                    raise ValueError(f"Unexpected change on page {j+1}")
        if profile["cutoff"].endswith("09-30") and len(data)>8_000_000:
            raise ValueError("A exceeds 8 MB")
    with output.open("xb") as handle:
        handle.write(data)
    print(output.name,len(data))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for opt in ("source","output","profile"):
        parser.add_argument(f"--{opt}",required=True,type=Path)
    args=parser.parse_args()
    revise(args.source,args.output,json.loads(args.profile.read_text(encoding="utf-8-sig")))


if __name__=="__main__":
    main()

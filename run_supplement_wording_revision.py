"""Apply the chapter 3 and 6 wording revision to a verified v4 PDF."""

import argparse
from collections import Counter
from pathlib import Path
import re

import fitz

from run_supplement_chapter_revision import FORBIDDEN, draw_table, flat, fonts, paragraph
from run_supplement_threshold_revision import overlay, rect_from_hits, redact


BALCONIES = "Balkone wegen Lärm und Staub nicht nutzbar"
DUST = (
    "Staubablagerungen auf Balkonen, Fenstern und Möbeln; regelmäßiger eigener "
    "Reinigungsaufwand (Fotos: Kapitel 8)"
)
OLD_PLAN = (
    "Lageplan: Abstand unter 4 m zur Brandwand und Sichtlinie vom Südostbalkon "
    "zu den eingezeichneten Bohrpositionen."
)
PLAN = (
    "Lageplan: Schlafzimmer, Kinderzimmer und Nordwestbalkon grenzen unmittelbar "
    "an das Baugrundstück (gemeinsame Brandwand, ca. 40 cm). Vom Südostbalkon "
    "beträgt der kürzeste waagrechte Abstand zur Baustelle rund 4 m. Eingezeichnet "
    "sind außerdem die Sichtlinie vom Südostbalkon und die Bohrpositionen."
)
CONVERSATION = "Gespräche in der Wohnung durch Baulärm erschwert"
SATURDAY = "Bauarbeiten auch an Samstagen"
DISTANCE = re.compile(r"\b(?:Abstand\w*|4\s*m\b|40\s*cm\b|Meter\w*)", re.IGNORECASE)


def impact_rows(page, box):
    return [flat(block[4]) for block in page.get_text("blocks", clip=box, sort=True)]


def patch_impacts(page, include_conversations):
    header = rect_from_hits(page, "Beeinträchtigung")
    early = rect_from_hits(page, "Arbeitsbeginn vor 7 Uhr")
    box = fitz.Rect(44, header.y0 - 6, page.rect.width - 31, early.y0 - 3)
    rows = impact_rows(page, box)
    if len(rows) != 14 or rows[0] != "Beeinträchtigung":
        raise ValueError("Expected a header and 13 v4 impact rows")
    if rows[8] != BALCONIES or rows[10] != DUST or not rows[6].startswith("Störungen am Abend"):
        raise ValueError("Unexpected text in v4 impact rows 6, 8 or 10")
    rows[8] += ", auch in den Sommermonaten"
    rows[10] = DUST.replace("Möbeln;", "Möbeln, auch in Innenräumen;")
    rows.insert(7, SATURDAY)
    if include_conversations:
        rows.insert(2, CONVERSATION)
    redact(page, box, graphics=True)

    def draw(canvas):
        top = page.rect.height - header.y0 + 5
        remaining = draw_table(
            canvas, [[row] for row in rows], top, [page.rect.width - 78], padding=2.2
        )
        if remaining + 12 < page.rect.height - box.y1:
            raise ValueError("Impact table overlaps the early-start section")

    overlay(page, draw)
    return box, rows


def patch_plan(page):
    heading = rect_from_hits(page, "6 Lage und datierte Baustellenansichten")
    old = rect_from_hits(page, OLD_PLAN)
    first_image = min(info["bbox"][1] for info in page.get_image_info())
    box = fitz.Rect(38, heading.y1 + 2, page.rect.width - 31, first_image - 0.5)
    if not box.contains(old):
        raise ValueError("v4 caption overlaps heading or graphics")
    redact(page, old + (-0.5, -0.5, 0.5, 0.5))

    def draw(canvas):
        caption = paragraph(PLAN, size=8.2)
        caption.style.leading = 9.1
        _, height = caption.wrap(page.rect.width - 78, box.height)
        if height > box.height:
            raise ValueError("New caption overlaps the map")
        caption.drawOn(canvas, 39, page.rect.height - box.y0 - height)

    overlay(page, draw)
    return box


def outside_text(page, box):
    return Counter(tuple(span["bbox"]) + (span["text"],) for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]
                   for line in block["lines"] for span in line["spans"] if not box.intersects(span["bbox"]))


def verify(before, after, changes, forbidden):
    if len(after) != len(before):
        raise ValueError("Page count changed")
    whole = flat(" ".join(page.get_text() for page in after)).casefold()
    for token in forbidden:
        if token.casefold() in whole:
            raise ValueError("Forbidden text found")
    if whole.count("gemeinsame brandwand, ca. 40 cm") != 1:
        raise ValueError("Expected exactly one common-wall caption")

    for index, (old, new) in enumerate(zip(before, after)):
        box = changes.get(index)
        if box is None:
            if old.get_text() != new.get_text() or old.get_pixmap().samples != new.get_pixmap().samples:
                raise ValueError(f"Unrequested change on page {index + 1}")
            continue
        if outside_text(old, box) != outside_text(new, box):
            raise ValueError(f"Text changed outside requested region on page {index + 1}")
        for clip in (fitz.Rect(0, 0, old.rect.width, box.y0 - 1),
                     fitz.Rect(0, box.y1 + 1, old.rect.width, old.rect.height)):
            if old.get_pixmap(clip=clip).samples != new.get_pixmap(clip=clip).samples:
                raise ValueError(f"Graphics changed outside requested region on page {index + 1}")
        for info in old.get_image_info():
            clip = fitz.Rect(info["bbox"])
            if old.get_pixmap(clip=clip).samples != new.get_pixmap(clip=clip).samples:
                raise ValueError(f"An image changed on page {index + 1}")


def revise(source, output, include_conversations, extra_forbidden):
    if output.exists():
        raise FileExistsError(output)
    fonts()
    with fitz.open(source) as before, fitz.open(source) as doc:
        impacts, rows = patch_impacts(doc[2], include_conversations)
        plan = patch_plan(doc[5])
        forbidden = FORBIDDEN + (
            "Prozent", "% Minderung", "wesentliche Beeinträchtigung gewertet",
            "Abstand unter 4 m zur Brandwand",
        ) + tuple(extra_forbidden)
        page_count = len(doc)
        payload = doc.tobytes(garbage=4, deflate=True)
        with fitz.open(stream=payload, filetype="pdf") as after:
            verify(before, after, {2: impacts, 5: plan}, forbidden)
            if impact_rows(after[2], impacts) != rows or flat(after[5].get_text(clip=plan)) != PLAN:
                raise ValueError("Revised text does not match in reading order")
            for page in after:
                for block in page.get_text("blocks", sort=True):
                    value = flat(block[4])
                    if DISTANCE.search(value):
                        print(f"Seite {page.number + 1}: {value}")
    with output.open("xb") as handle:
        handle.write(payload)
    print(f"{output.name}: {page_count} Seiten, {output.stat().st_size} Byte; {len(rows)-1} Tabellenzeilen; QA OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-conversations", action="store_true")
    parser.add_argument("--forbidden", action="append", default=[])
    args = parser.parse_args()
    revise(args.source, args.output, args.include_conversations, args.forbidden)


if __name__ == "__main__":
    main()

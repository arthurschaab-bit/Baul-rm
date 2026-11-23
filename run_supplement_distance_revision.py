"""Replace the Southeast balcony distance sentence in a verified v7 PDF."""

import argparse
from pathlib import Path
import re

import fitz

from run_supplement_chapter_revision import flat, fonts, paragraph
from run_supplement_threshold_revision import overlay


OLD = "Der kürzeste waagrechte Abstand vom Südostbalkon zur Baustelle beträgt rund 4 m."
NEW = "Der Südostbalkon liegt wenige Meter von der Baustelle entfernt (siehe Lageplan, Maßstab oben rechts)."
DISTANCE = re.compile(r"(?<![\d,])4\s+m\b|<\s*4\b|\b4\s+Meter\b|\brund\s+4\b", re.IGNORECASE)


def revise(source, output, totals):
    fonts()
    with fitz.open(source) as old, fitz.open(source) as doc:
        page = doc[5]
        blocks = [b for b in page.get_text("blocks") if OLD in flat(b[4])]
        if len(blocks) != 1:
            raise ValueError("Expected one distance paragraph on page 6")
        changed = flat(blocks[0][4]).replace(OLD, NEW)
        map_top = min(info["bbox"][1] for info in page.get_image_info())
        box = fitz.Rect(38, 71, page.rect.width - 32, map_top - 1)
        if not box.contains(fitz.Rect(blocks[0][:4])):
            raise ValueError("The distance paragraph is outside the expected area")
        page.add_redact_annot(box, fill=False)
        page.apply_redactions(images=0, graphics=0, text=0)

        def draw(canvas):
            item = paragraph(changed, 9)
            width = page.rect.width - 78
            _, height = item.wrap(width, 1000)
            if 73 + height >= map_top:
                raise ValueError("The revised paragraph would overlap the map")
            item.drawOn(canvas, 39, page.rect.height - 73 - height)

        overlay(page, draw)
        data = doc.tobytes(garbage=4, deflate=True)
        with fitz.open(stream=data, filetype="pdf") as final:
            if len(old) != len(final) or len(final) != 15:
                raise ValueError("Unexpected page count")
            text = flat(" ".join(p.get_text() for p in final))
            if DISTANCE.search(text) or text.count(NEW) != 1:
                raise ValueError("Unexpected remaining or duplicate distance wording")
            for index, (before, after) in enumerate(zip(old, final)):
                if index == 5:
                    if flat(after.get_text(clip=box)) != changed:
                        raise ValueError("Unexpected Chapter 6 wording")
                    clips = (fitz.Rect(0, 0, before.rect.width, box.y0-1),
                             fitz.Rect(0, box.y1+1, before.rect.width, before.rect.height))
                else:
                    if before.get_text() != after.get_text():
                        raise ValueError(f"Text changed on page {index+1}")
                    clips = (before.rect,)
                for clip in clips:
                    if before.get_pixmap(clip=clip).samples != after.get_pixmap(clip=clip).samples:
                        raise ValueError(f"Unexpected layout change on page {index+1}")
            for count, threshold in zip(totals, (63, 70)):
                if f"{count} Bautage über {threshold} dB(A)" not in flat(final[1].get_text()):
                    raise ValueError("Unexpected threshold count")
    with output.open("xb") as file:
        file.write(data)
    print(f"{output.name}: 15 Seiten, {len(data)} Byte; geprüft")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--totals", required=True, type=int, nargs=2, metavar=("OVER_63", "OVER_70"))
    args = parser.parse_args()
    revise(args.source, args.output, args.totals)


if __name__ == "__main__":
    main()

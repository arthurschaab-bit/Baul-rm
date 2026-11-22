"""Update only the requested chapters of a locally verified v2 report."""
import argparse
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re

import fitz
from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

from run_supplement_chapter_revision import (
    FORBIDDEN, INTRO_3, draw_paragraph, find_chapter, flat, fonts, paragraph, table_rows,
)

INTRO = (
    "Die Bauarbeiten beeinträchtigen die Nutzung der Wohnung über die gemessenen Pegel hinaus. "
    "Bautagebuch und Fotodokumentation beschreiben insbesondere:"
)
COURT = (
    "Die Zählung der Tage über 63 dB(A) und über 70 dB(A) folgt der Darstellung in AG München, "
    "Urteil vom 01.02.2018 – 472 C 18927/16 (bestätigt durch LG München I, Urteil vom 15.11.2018 – "
    "31 S 2182/18). Dort wurden Einwirkungen von über 63 dB an der Wohnung als wesentliche "
    "Beeinträchtigung gewertet und Tage über 70 dB gesondert ausgewiesen. Tage mit 70–89 % "
    "Abdeckung zählen nur, wenn bereits die rechnerische Untergrenze die Schwelle überschreitet."
)
LEGEND = ("Fett: über 70 dB(A). Unterstrichen: über 63 bis einschließlich 70 dB(A). "
          "Die Einstufung verwendet ungerundete Tageswerte.")
THRESHOLDS = (63, 70)


def threshold_dates(rows):
    """Apply strict inequality directly to the unrounded day metrics."""
    return {
        str(t): {
            group: sorted(day for day, m in rows.items()
                          if m["group"] == group and m[field] > t)
            for group, field in (("a", "laeq"), ("b", "lower"))
        }
        for t in THRESHOLDS
    }


def rect_from_hits(page, value):
    rects = page.search_for(value)
    if not rects:
        raise ValueError(f"Text absent on page {page.number + 1}: {value}")
    return fitz.Rect(min(r.x0 for r in rects), min(r.y0 for r in rects),
                     max(r.x1 for r in rects), max(r.y1 for r in rects))


def redact(page, box, graphics=False):
    page.add_redact_annot(box, fill=False)
    page.apply_redactions(images=0, graphics=1 if graphics else 0, text=0)


def overlay(page, draw):
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(page.rect.width, page.rect.height), pageCompression=1)
    draw(canvas)
    canvas.showPage()
    canvas.save()
    with fitz.open(stream=stream.getvalue(), filetype="pdf") as patch:
        page.show_pdf_page(page.rect, patch, 0)


def cards(canvas, page, base_text, counts):
    w,h=page.rect.width,page.rect.height
    title_index=next(i for i,x in enumerate(base_text) if x.startswith("2 Kernbefunde"))
    x=base_text[title_index+1:]
    if len(x)<13:raise ValueError("Base summary cards not recognized")
    a=[(x[1],x[3]), (x[4],x[6]), (x[5],x[7])]
    comparison=(f"{x[8]}  /  {x[9]}",f"{x[10]}  /  {x[11]}")
    top=[(f"{sum(counts[str(t)])} Bautage über {t} dB(A)",
          f"vollständig erfasst: {counts[str(t)][0]}; weitere mit rechnerischer Untergrenze: {counts[str(t)][1]}")
         for t in THRESHOLDS]
    rows=[top,a[:2],[a[2],comparison]]
    y=draw_paragraph(canvas,"2  Kernbefunde",h-40,w-78,17,True)
    width=(w-78)/2
    for items in rows:
        height=56
        canvas.setFillColor(colors.HexColor("#e9f1f1"))
        canvas.rect(39,y-height,w-78,height,stroke=0,fill=1)
        for i,(big,small) in enumerate(items):
            left=47+i*width
            for content,size,bold,top_y in ((big,15,True,y-5),(small,7.5,False,y-27)):
                obj=paragraph(content,size,bold)
                _,extent=obj.wrap(width-16,1000)
                obj.drawOn(canvas,left,top_y-extent)
        y-=height+7
    y=draw_paragraph(canvas,COURT,y,w-78,7.7)
    y=draw_paragraph(canvas,f"{x[0]} · {x[2]}",y,w-78,8.5,True)
    for t in x[12:]:
        y=draw_paragraph(canvas,t,y,w-78,8.6)
    if y<48:raise ValueError("Summary exceeds safe page height")


def summary_blocks(page):
    ordered=[]
    for b in page.get_text("blocks"):
        if len(b)>4 and b[1]<page.rect.height-45:
            ordered.append((b[1],b[0],str(b[4]).splitlines()))
    # Each pair of cards is represented by two text blocks with one line per card.
    result=[]
    for _,_,lines in sorted(ordered):
        if not result or len(result)<13:
            result.extend(flat(x) for x in lines if flat(x))
        else:
            result.append(flat(" ".join(lines)))
    return result


def patch_intro(page):
    old_box=rect_from_hits(page,INTRO_3)
    redact(page,old_box+(-1,-1,1,1))
    overlay(page,lambda c:draw_paragraph(c,INTRO,page.rect.height-old_box.y0+1,page.rect.width-78))


def level_tables(page):
    return [t for t in page.find_tables().tables if flat(t.extract()[0][0])=="Datum"]


def level_rows(page):
    return [list(map(flat,r)) for t in level_tables(page) for r in t.extract()[1:]]


def mark_levels(page, metrics):
    source=level_tables(page)
    before=level_rows(page)
    marks=[]
    for table in source:
        for i,row in enumerate(table.extract()[1:],1):
            m=re.search(r"\d{2}\.\d{2}\.\d{4}",row[0])
            if not m:raise ValueError("A table date is absent")
            day=datetime.strptime(m[0],"%d.%m.%Y").date().isoformat()
            data=metrics[day]
            if data["group"]!="a":raise ValueError(f"Chapter 9 lists non-a day {day}")
            raw=data["laeq"]
            if raw<=63:continue
            cell=fitz.Rect(table.rows[i].cells[1])
            spans=[s for b in page.get_text("dict",clip=cell)["blocks"]
                   for l in b.get("lines",[]) for s in l["spans"] if flat(s["text"])==flat(row[1])]
            if len(spans)!=1:raise ValueError(f"Cannot locate value on {day}")
            s=spans[0]; marks.append((raw,s))
    for raw,s in marks:
        if raw>70:page.add_redact_annot(fitz.Rect(s["bbox"]),fill=False)
    page.apply_redactions(images=0,graphics=0,text=0)
    def draw(canvas):
        h=page.rect.height
        for raw,s in marks:
            x,y=s["origin"]
            dark=colors.HexColor("#172d38")
            canvas.setFillColor(dark)
            canvas.setStrokeColor(dark)
            if raw>70:
                canvas.setFont("Base-Bold",s["size"])
                canvas.drawString(x,h-y,s["text"])
            else:
                canvas.setLineWidth(.5)
                canvas.line(x,h-y-1,s["bbox"][2],h-y-1)
        bottom=max(b[3] for b in page.get_text("blocks") if len(b)>4 and b[1]<h-45)
        draw_paragraph(canvas,LEGEND,h-bottom-11,page.rect.width-78,7.4)
    overlay(page,draw)
    if level_rows(page)!=before:raise ValueError("Chapter 9 table text changed")
    return marks


def patch_groups(page,counts):
    original=next(t for t in page.find_tables().tables if flat(t.extract()[0][0])=="Gruppe")
    rows=[[flat(c) for c in row] for row in original.extract()]
    pos=fitz.Rect(original.bbox)
    new=[]
    for i,row in enumerate(rows):
        parts=[f"über {n} dB(A)" for n in THRESHOLDS] if i==0 else [
            str(counts[str(n)][i-1] if i<3 else sum(counts[str(n)])) if i<4 else "–"
            for n in THRESHOLDS]
        new.append(row[:4]+[parts[0],row[4],parts[1],row[5]])
    redact(page,pos+(-.4,-.4,.4,.4),graphics=True)
    def draw(canvas):
        vals=[[paragraph(v,6.4,i==0) for v in row] for i,row in enumerate(new)]
        widths=[172,35,49,49,49,49,49,pos.width-452]
        table=Table(vals,colWidths=widths,rowHeights=[20,13,13,13,pos.height-59])
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e9f1f1")),
            ("GRID",(0,0),(-1,-1),.25,colors.lightgrey),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),3),
            ("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),0),
            ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))
        table.wrapOn(canvas,pos.width,pos.height)
        table.drawOn(canvas,pos.x0,page.rect.height-pos.y1)
    overlay(page,draw)
    if table_rows(page,"Gruppe")!=new:raise ValueError("Rendered groups or sums differ")
    return new


def add_evidence_photo(pdf, definition):
    """Leave the plan and other chapters intact while inserting one dated photo."""
    from PIL import Image, ImageOps

    page_number = next(p.number for p in pdf if p.get_text().find("6 Lage und datierte Baustellenansichten") >= 0)
    main = pdf[page_number]
    continuation = page_number + 1 < len(pdf) and "(Fortsetzung)" in pdf[page_number+1].get_text().splitlines()[2]
    # Use the continuation if present. The short report has room for a third
    # picture in the existing photo row, with the three captions below it.
    page = pdf[page_number+1] if continuation else main
    with Image.open(definition["path"]) as source:
        photo = ImageOps.exif_transpose(source).convert("RGB")
        photo.thumbnail((1700,1700),Image.Resampling.LANCZOS)
        photo_bytes = BytesIO()
        photo.save(photo_bytes,format="JPEG",quality=90,optimize=True)
    if continuation:
        left,top,width,height = 49,222,204,285
        page.insert_image(fitz.Rect(left,top,left+width,top+height),stream=photo_bytes.getvalue(),keep_proportion=True)
        def caption(canvas):
            item = paragraph(definition["caption"], 7.5)
            item.wrap(width, 20)
            item.drawOn(canvas, left, page.rect.height-top-height-14)
        overlay(page, caption)
    else:
        existing=sorted(page.get_image_info(xrefs=True),key=lambda v:v["bbox"][1])[1:]
        if len(existing)!=2:raise ValueError("Expected two existing photographs")
        box=fitz.Rect(44,min(x["bbox"][1] for x in existing)-4,page.rect.width-33,max(x["bbox"][3] for x in existing)+22)
        captions=[flat(t[4]) for t in page.get_text("blocks",clip=box) if len(t)>4]
        stamps=re.findall(r"\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2} Uhr", " ".join(captions))
        if len(stamps)!=2:raise ValueError("Base photo captions not recognized")
        blobs=[pdf.extract_image(item["xref"])["image"] for item in sorted(existing,key=lambda v:v["bbox"][0])]
        page.add_redact_annot(box,fill=(1,1,1))
        page.apply_redactions(images=1,graphics=1,text=0)
        span=(page.rect.width-88)/3
        top=box.y0+5;bottom=box.y1-19
        for i,blob in enumerate(blobs+[photo_bytes.getvalue()]):
            left=49+i*span
            page.insert_image(fitz.Rect(left,top,left+span-9,bottom),stream=blob,keep_proportion=True)
        def caption_row(canvas):
            for i,cap in enumerate(stamps+[definition["caption"]]):
                item=paragraph(cap,7.5);item.wrap(span-9,20);item.drawOn(canvas,49+i*span,page.rect.height-bottom-15)
        overlay(page,caption_row)
    return page.number

def revise(source,output,data):
    if output.exists():raise FileExistsError(output)
    fonts()
    counts={t:[len(v[g]) for g in "ab"] for t,v in threshold_dates(data["rows"]).items()}
    candidate=output.with_suffix(".candidate.pdf")
    with fitz.open(source) as pdf:
        indices=[find_chapter(pdf,t) for t in ("2 Kernbefunde","3 Auswirkungen auf die Wohnnutzung",
                    "9 Vollständig erfasste Außentage" if "9 Vollständig erfasste Außentage" in flat(" ".join(p.get_text() for p in pdf)) else "9 Vollständig erfasste Außen-Bautage",
                    "10 Alle Messtage nach Abdeckung")]
        c2,c3,c9,c10=indices
        text2=summary_blocks(pdf[c2])
        impacts_before=flat(pdf[c3].get_text())
        redact(pdf[c2],fitz.Rect(0,30,pdf[c2].rect.width,pdf[c2].rect.height-42),graphics=True)
        overlay(pdf[c2],lambda c:cards(c,pdf[c2],text2,counts))
        patch_intro(pdf[c3])
        if flat(pdf[c3].get_text()).split("Beeinträchtigung ",1)[1].split("Arbeitsbeginn vor 7 Uhr",1)[0].strip()!=impacts_before.split("Beeinträchtigung ",1)[1].split("Arbeitsbeginn vor 7 Uhr",1)[0].strip():
            raise ValueError("The 13 impact rows changed")
        marks=mark_levels(pdf[c9],data["rows"])
        if len(marks)!=counts["63"][0] or sum(x>70 for x,_ in marks)!=counts["70"][0]:
            raise ValueError("Chapter 9 highlight counts differ")
        groups=patch_groups(pdf[c10],counts)
        if data.get("photo"):
            indices.append(add_evidence_photo(pdf,data["photo"]))
        pdf.save(candidate,garbage=4,deflate=True,use_objstms=1)
    with fitz.open(source) as before,fitz.open(candidate) as after:
        if len(before)!=len(after):raise ValueError("Page count changed")
        for i,(a,b) in enumerate(zip(before,after)):
            if i not in indices and (a.get_text()!=b.get_text() or a.get_pixmap().samples!=b.get_pixmap().samples):
                raise ValueError(f"Unchanged page {i+1} differs")
            clip=fitz.Rect(0,a.rect.height-41,a.rect.width,a.rect.height)
            if a.get_pixmap(clip=clip).samples!=b.get_pixmap(clip=clip).samples:
                raise ValueError(f"Footer differs on page {i+1}")
        if table_rows(before[c3],"Datum")!=table_rows(after[c3],"Datum"):raise ValueError("Early starts changed")
        text2_new=flat(after[c2].get_text())
        for old in text2[1:]:
            if old not in text2_new:raise ValueError(f"Missing existing chapter 2 text: {old}")
        for t in THRESHOLDS:
            if f"{sum(counts[str(t)])} Bautage über {t} dB(A)" not in text2_new:
                raise ValueError("Chapter 2 totals differ")
        text=flat(" ".join(p.get_text() for p in after))
        for token in FORBIDDEN+("Prozent","% Minderung")+tuple(data.get("forbidden",())):
            if token.casefold() in text.casefold():raise ValueError(f"Forbidden token: {token}")
        for cite in ("472 C 18927/16","31 S 2182/18"):
            if text.count(cite)!=1:raise ValueError(f"Invalid reference multiplicity: {cite}")
        if not all(x in text2_new for x in (COURT,)):raise ValueError("Court wording differs")
        count=len(after)
    candidate.rename(output)
    return {"file":output.name,"pages":count,"edited_pages":[i+1 for i in sorted(indices)],"counts":counts,"groups":groups}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--metrics",type=Path,required=True)
    parser.add_argument("--stand",required=True)
    args=parser.parse_args()
    profile=json.loads(args.metrics.read_text(encoding="utf-8-sig"))[args.stand]
    print(json.dumps(revise(args.source,args.output,profile),ensure_ascii=False))


if __name__=="__main__":
    main()

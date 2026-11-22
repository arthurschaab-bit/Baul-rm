"""Render the displayed-threshold and photo revision from verified local PDFs."""
import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import fitz
from PIL import Image, ImageOps

import run_supplement_threshold_revision as previous
from run_supplement_chapter_revision import (
    FORBIDDEN, draw_paragraph, draw_table, flat, fonts, paragraph, table_rows,
)

COURT = (
    "Die Zählung der Tage über 63 dB(A) und über 70 dB(A) lehnt sich an die Darstellung in AG München, "
    "Urteil vom 01.02.2018 – 472 C 18927/16, an (Berufung zurückgewiesen durch LG München I, "
    "Urteil vom 15.11.2018 – 31 S 2182/18). Nach der Pressemitteilung des Amtsgerichts vom 14.12.2018 "
    "stellte das Gericht auf Tage mit Einwirkungen von über 63 dB sowie über 70 dB an der Wohnung ab. "
    "Tage mit 70–89 % Abdeckung zählen hier nur, wenn bereits die rechnerische Untergrenze die Schwelle überschreitet."
)
IMPACTS = (
    "Verschattung und Sichtbeeinträchtigung: Schlafzimmerfenster und Südostbalkon durch Gerüst mit Schutzplane verdeckt (Fotos: Kapitel 6)",
    "Gerüst mit Schutzplane am Mietobjekt über mehrere Monate (Fotos: Kapitel 6)",
)
LEGEND = (
    "Fett: über 70 dB(A). Unterstrichen: über 63 bis einschließlich 70 dB(A). "
    "Maßgeblich ist der auf eine Nachkommastelle gerundete Tabellenwert."
)
THRESHOLDS = (55, 60, 63, 65, 70)
CONTINUATION = "6 Lage und datierte Baustellenansichten (Fortsetzung)"
TZ=ZoneInfo("Europe/Berlin")


def display_value(value):
    """Apply the same numeric formatting as printed day levels."""
    return float(f"{value:.1f}")


def comparisons(rows):
    result={}
    for threshold in THRESHOLDS:
        groups={}
        for group in ("a","b"):
            # Preserve each v3 column's measure. The two introduced thresholds
            # use the lower bound for group b. The earlier columns use LAeq.
            field="lower" if group=="b" and threshold in (63,70) else "laeq"
            relevant={day:row[field] for day,row in rows.items() if row["group"]==group}
            old=[day for day,value in relevant.items() if value>threshold]
            new=[day for day,value in relevant.items() if display_value(value)>threshold]
            groups[group]={"old":sorted(old),"new":sorted(new),
                           "changed":{day:relevant[day] for day in sorted(set(old)^set(new))}}
        result[str(threshold)]=groups
    return result


def photo(path, cutoff):
    with Image.open(path) as original:
        exif=original.getexif().get_ifd(34665)
        local=datetime.strptime(exif[36867]+" "+exif[36881],"%Y:%m:%d %H:%M:%S %z").astimezone(TZ)
        filetime=datetime.strptime(path.name[4:19],"%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).astimezone(TZ)
        if local!=filetime or local.date().isoformat()>cutoff:
            raise ValueError(f"EXIF inconsistent with original filename or cutoff: {path.name}")
        img=ImageOps.exif_transpose(original).convert("RGB")
        img.thumbnail((1120,1120),Image.Resampling.LANCZOS)
        buf=BytesIO()
        img.save(buf,"JPEG",quality=84,optimize=True)
        return (buf.getvalue(),img.width/img.height,local.strftime("%d.%m.%Y, %H:%M Uhr"),local.isoformat())


def draw_photos(page, photos, new_page):
    width=page.rect.width
    if new_page:
        cols=3
        top=83
        max_height=258
    else:
        cols=2
        top=215
        max_height=246
    span=(width-90)/cols
    slots=[]
    for i,(blob,ratio,caption,_) in enumerate(photos):
        x=45+i%cols*span
        y=top+i//cols*(max_height+35)
        h=min(max_height,(span-12)/ratio)
        w=h*ratio
        target=fitz.Rect(x+(span-w)/2,y,x+(span+w)/2,y+h)
        page.insert_image(target,stream=blob)
        slots.append((target.x0,target.y1,caption))
    def text(c):
        if new_page:
            draw_paragraph(c,CONTINUATION,page.rect.height-40,width-78,17,True)
            label=paragraph("Belastungsdokumentation · Auswertestand 30.09.2026",7)
            label.wrap(250,15)
            label.drawOn(c,39,18)
            c.setFont("Base",7)
            c.drawRightString(width-39,19,"6a")
        for x,y,caption in slots:
            item=paragraph(caption,7.5)
            item.wrap(span-8,20)
            item.drawOn(c,x,page.rect.height-y-14)
    previous.overlay(page,text)


def swap_page(doc, index, donor, donor_index):
    doc.delete_page(index)
    doc.insert_pdf(donor,from_page=donor_index,to_page=donor_index,start_at=index)


def patch_impacts(page, new_rows):
    body=flat(page.get_text())
    old=body.split("Beeinträchtigung ",1)[1].split("Arbeitsbeginn vor 7 Uhr",1)[0].strip()
    if flat(" ".join(new_rows[:11])) not in old:
        raise ValueError("First eleven items disagree with v3")
    start=previous.rect_from_hits(page,new_rows[0]).y0
    header=previous.rect_from_hits(page,"Beeinträchtigung")
    early=next(flat(x) for x in page.get_text().splitlines() if x.startswith("Arbeitsbeginn vor 7 Uhr"))
    finish=previous.rect_from_hits(page,early).y0
    previous.redact(page,fitz.Rect(44,min(header.y0,start)-6,page.rect.width-31,finish-3),graphics=True)
    def draw(c):
        y=page.rect.height-header.y0+5
        rest=draw_table(c,[["Beeinträchtigung"],*[[x] for x in new_rows]],y,[page.rect.width-78],padding=2.4)
        if rest+10<page.rect.height-finish:raise ValueError("Impact rows overlap early-start header")
    previous.overlay(page,draw)


def revise(base_path,layout_path,output,profile,metrics,cutoff):
    if output.exists():raise FileExistsError(output)
    fonts()
    previous.COURT=COURT
    previous.LEGEND=LEGEND
    for_file=comparisons(metrics["rows"])
    counts={t:[len(q[g]["new"]) for g in ("a","b")] for t,q in for_file.items()}
    rounded={day:dict(row,laeq=display_value(row["laeq"])) for day,row in metrics["rows"].items()}
    photos=[photo(Path(x),cutoff) for x in profile["photos"]]
    expected=3 if cutoff.endswith("09-30") else 4
    if len(photos)!=expected:raise ValueError("Unexpected scaffold photo count")
    with fitz.open(base_path) as base,fitz.open(layout_path) as layout:
        old2,old3,old6,old9,old10=[
            next(p.number for p in base if any(x.startswith(prefix) for x in p.get_text().splitlines()))
            for prefix in ("2 Kernbefunde","3 Auswirkungen","6 Lage und datierte","9 Vollständig erfasste","10 Alle Messtage")
        ]
        measurements=previous.summary_blocks(layout[old2])
        group_table=table_rows(base[old10],"Gruppe")
        old_group_labels=group_table[0]
        threshold_cols={n:old_group_labels.index(f"über {n} dB(A)") for n in THRESHOLDS}
        for n,col in threshold_cols.items():
            for i,g in enumerate(("a","b"),1):
                if int(group_table[i][col])!=len(for_file[str(n)][g]["old"]):
                    raise ValueError(f"v3 group count does not match baseline at {n}/{g}")
        previous.redact(base[old2],fitz.Rect(0,30,base[old2].rect.width,base[old2].rect.height-42),graphics=True)
        previous.overlay(base[old2],lambda c:previous.cards(c,base[old2],measurements,counts))
        patch_impacts(base[old3],profile["impacts"][:11]+list(IMPACTS))
        swap_page(base,old9,layout,old9)
        highlights=previous.mark_levels(base[old9],rounded)
        if len(highlights)!=counts["63"][0] or sum(n>70 for n,_ in highlights)!=counts["70"][0]:
            raise ValueError("Chapter 9 mark count differs from displayed threshold counts")
        swap_page(base,old10,layout,old10)
        previous.patch_groups(base[old10],counts)
        if expected==3:
            swap_page(base,old6,layout,old6)
            page=base.new_page(pno=old6+1,width=base[old6].rect.width,height=base[old6].rect.height)
            draw_photos(page,photos,True)
            image_index=old6+1
            new_page=image_index
            edited={old2,old3,old6,old9,old10}
        else:
            page=base[old6+1]
            images=page.get_image_info()
            if len(images)!=4:raise ValueError("Expected three retained images and one scaffold image")
            cap=previous.rect_from_hits(page,photos[1][2])
            image=fitz.Rect(images[-1]["bbox"])
            box=fitz.Rect(0,image.y0-4,page.rect.width,max(image.y1,cap.y1)+3)
            page.add_redact_annot(box,fill=(1,1,1))
            page.apply_redactions(images=1,graphics=0,text=0)
            draw_photos(page,photos,False)
            image_index=old6+1
            new_page=None
            edited={old2,old3,old6+1,old9,old10}
        candidate=output.with_suffix(".candidate.pdf")
        base.save(candidate,garbage=4,deflate=True,use_objstms=1)
    with fitz.open(base_path) as old,fitz.open(candidate) as final:
        if len(final)!=len(old)+(new_page is not None):raise ValueError("Unexpected page count")
        def index(i):return i+int(new_page is not None and i>=new_page)
        for i,pg in enumerate(old):
            fresh=final[index(i)]
            if i not in edited and (pg.get_text()!=fresh.get_text() or pg.get_pixmap().samples!=fresh.get_pixmap().samples):
                raise ValueError(f"Unchanged page differs: {i+1}")
            clip=fitz.Rect(0,pg.rect.height-41,pg.rect.width,pg.rect.height)
            if pg.get_pixmap(clip=clip).samples!=fresh.get_pixmap(clip=clip).samples:
                raise ValueError(f"Old footer changed at {i+1}")
        alltext=flat(" ".join(p.get_text() for p in final))
        for term in FORBIDDEN+("Prozent","% Minderung","wesentliche Beeinträchtigung gewertet")+tuple(metrics["forbidden"]):
            if term.casefold() in alltext.casefold():raise ValueError(f"Forbidden token: {term}")
        for term in ("472 C 18927/16","31 S 2182/18","Pressemitteilung"):
            if alltext.count(term)!=1:raise ValueError(f"Reference multiplicity: {term}")
        if COURT not in flat(final[index(old2)].get_text()):raise ValueError("Court wording differs")
        new_3=flat(final[index(old3)].get_text())
        actual=new_3.split("Beeinträchtigung ",1)[1].split("Arbeitsbeginn vor 7 Uhr",1)[0].strip()
        if actual!=flat(" ".join(profile["impacts"][:11]+list(IMPACTS))) or "Beispiele (Datum)" in new_3:
            raise ValueError("Impact wording or count differs")
        if table_rows(old[old3],"Datum")!=table_rows(final[index(old3)],"Datum"):raise ValueError("Early table differs")
        if len(final[image_index].get_image_info())!=len(photos)+(0 if new_page is not None else 3):
            raise ValueError("Wrong number of retained or new photos")
        page_text=flat(final[image_index].get_text())
        for stamp in {x[2] for x in photos}:
            if page_text.count(stamp)!=sum(x[2]==stamp for x in photos):raise ValueError("EXIF caption mismatch")
        new2=flat(final[index(old2)].get_text())
        group=table_rows(final[index(old10)],"Gruppe")
        for n in THRESHOLDS:
            col=threshold_cols[n]
            if [int(group[i][col]) for i in (1,2,3)]!=counts[str(n)]+[sum(counts[str(n)])]:
                raise ValueError(f"Chapter 10 count mismatch at {n}")
            if n in (63,70) and f"{sum(counts[str(n)])} Bautage über {n} dB(A)" not in new2:
                raise ValueError(f"Chapter 2 count mismatch at {n}")
        if candidate.stat().st_size>profile["max_bytes"]:raise ValueError("PDF exceeds size limit")
        pagecount=len(final)
    candidate.rename(output)
    return dict(file=output.name,pages=pagecount,size=output.stat().st_size,photo_page=image_index+1,
                comparison=for_file,photos=[{"file":Path(p).name,"local_exif":v[3]} for p,v in zip(profile["photos"],photos)])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("base","layout","profile","metrics","output"):
        p.add_argument("--"+key,type=Path,required=True)
    p.add_argument("--stand",required=True)
    a=p.parse_args()
    read=lambda p:json.loads(p.read_text(encoding="utf-8-sig"))[a.stand]
    print(json.dumps(revise(a.base,a.layout,a.output,read(a.profile),read(a.metrics),a.stand),ensure_ascii=False))


if __name__=="__main__":
    main()

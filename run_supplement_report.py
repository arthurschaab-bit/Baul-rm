from __future__ import annotations
import csv,json,math,datetime as dt
from pathlib import Path
from io import BytesIO
import numpy as np,pandas as pd,yaml,fitz
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image as PILImage
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,Image,KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import argparse
import os

parser=argparse.ArgumentParser(description='Ergänzung aus einer lokal geprüften Datenbasis erzeugen.')
parser.add_argument('--data-dir',type=Path,required=True)
parser.add_argument('--profile',type=Path,required=True)
parser.add_argument('--assets-manifest',type=Path,required=True)
parser.add_argument('--stand',required=True)
args=parser.parse_args()
DATA=OUT=args.data_dir
AS=OUT/'assets'
AS.mkdir(parents=True,exist_ok=True)
CFG=yaml.safe_load(args.profile.read_text(encoding='utf-8-sig'))
MEDIA=json.loads(args.assets_manifest.read_text(encoding='utf-8-sig'))
FRAME=pd.read_pickle(DATA/'signal.pkl')
assert set(FRAME.source)=={'PCE_323'} and FRAME.leq.max()<=100
fonts=Path(os.environ['SystemRoot'])/'Fonts' if os.name=='nt' else Path('/usr/share/fonts/truetype/dejavu')
FONT=fonts/'DejaVuSans.ttf';BOLD=fonts/'DejaVuSans-Bold.ttf'
if not FONT.exists():FONT=fonts/'arial.ttf';BOLD=fonts/'arialbd.ttf'
pdfmetrics.registerFont(TTFont('Base',str(FONT)));pdfmetrics.registerFont(TTFont('Base-Bold',str(BOLD)))
W,H=A4;M=39;CW=W-2*M;black=colors.HexColor('#172d38');teal=colors.HexColor('#12666b');muted=colors.HexColor('#4f6167');pale=colors.HexColor('#e9f1f1')
styles={
 'title':ParagraphStyle('title',fontName='Base-Bold',fontSize=23,leading=29,textColor=black,spaceAfter=13),
 'h1':ParagraphStyle('h1',fontName='Base-Bold',fontSize=17,leading=21,textColor=black,spaceAfter=11),
 'h2':ParagraphStyle('h2',fontName='Base-Bold',fontSize=11,leading=15,textColor=teal,spaceBefore=7,spaceAfter=5),
 'body':ParagraphStyle('body',fontName='Base',fontSize=9.0,leading=13.3,textColor=black,spaceAfter=7),
 'small':ParagraphStyle('small',fontName='Base',fontSize=7.5,leading=10.2,textColor=muted,spaceAfter=4),
 'big':ParagraphStyle('big',fontName='Base-Bold',fontSize=18,leading=23,textColor=teal,spaceAfter=3),
 'cell':ParagraphStyle('cell',fontName='Base',fontSize=7.1,leading=9.7,textColor=black),
 'cellhead':ParagraphStyle('cellhead',fontName='Base-Bold',fontSize=7.1,leading=9.7,textColor=black),
}
def P(s,style='body'):
 from xml.sax.saxutils import escape
 return Paragraph(escape(str(s)),styles[style])
def de(x,n=1):return f'{float(x):.{n}f}'.replace('.',',')
def day(s):return dt.date.fromisoformat(s).strftime('%d.%m.%Y')
def num(x):return f'{int(x):,}'.replace(',','.')
def date_start(s):return int(pd.Timestamp(s+' 07:00',tz='Europe/Berlin').timestamp())
def series_day(s,start_hour=7,end_hour=20):
 a=int(pd.Timestamp(s+f' {start_hour:02d}:00',tz='Europe/Berlin').timestamp());b=int(pd.Timestamp(s+f' {end_hour:02d}:00',tz='Europe/Berlin').timestamp())
 return FRAME.loc[(FRAME.index>=a)&(FRAME.index<b),'leq']
def enmean(x):
 v=np.asarray(x,dtype=float);return float(10*np.log10(np.mean(10**(v/10)))) if len(v) else None
def plot_image(fig,name,width=CW):
 path=AS/name;fig.savefig(path,dpi=180,bbox_inches='tight',facecolor='white');plt.close(fig);im=PILImage.open(path);return Image(str(path),width=width,height=width*im.height/im.width)
def fit_image(path,w,maxh):
 with PILImage.open(path) as im:
  ratio=im.width/im.height
  h=min(maxh,w/ratio)
  if max(im.size)>2400 and path.suffix.lower() in ('.jpg','.jpeg'):
   copy=AS/('kompakt_'+path.name)
   rgb=im.convert('RGB')
   rgb.thumbnail((2400,2400),PILImage.Resampling.LANCZOS)
   rgb.save(copy,'JPEG',quality=90,optimize=True,subsampling=0)
   path=copy
 return Image(str(path),width=h*ratio,height=h)
def table(rows,widths,size=7.1):
 t=Table(rows,colWidths=widths,hAlign='LEFT',repeatRows=1)
 t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),pale),('GRID',(0,0),(-1,-1),.25,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
 return t
map_target=Path(MEDIA['map'])

def render(cutoff):
 D=json.loads((DATA/('zahlen_bis_'+cutoff+'.json')).read_text(encoding='utf-8'))
 K=json.loads((DATA/'klassifikation.json').read_text(encoding='utf-8'));g=D['varianten']['A'];high=[r for r in g['tage'] if r['tag'] in K['kategorien']['a']['tage']]
 median=D['median_bautag'];top=D['untergrenzen_top5'];work=[r for r in high if dt.date.fromisoformat(r['tag']).weekday()<5 and r['tag'] not in {'2026-06-25','2026-06-26'}]
 a_by={x['tag']:x for x in g['tage']}
 b_by={x['tag']:x for x in D['varianten']['B']['tage']}
 nw=[x for x in high if x['tag'] in {'2026-06-25','2026-06-26'}]
 sat=[x for x in high if dt.date.fromisoformat(x['tag']).weekday()==5]
 assert len(work)+len(nw)+len(sat)==len(high)
 other=[(abs(x['laeq_db']-b_by[x['tag']]['laeq_db']),b_by[x['tag']]) for x in work if x['tag'] in b_by and abs(x['laeq_db']-b_by[x['tag']]['laeq_db'])>=1]
 assert D['wettervergleich']['vergleichbar']-len(other)==D['wettervergleich']['delta_unter_1_db']
 lowaug=a_by['2026-08-17']
 story=[]
 def add(*x):story.extend(x)
 def page():story.append(PageBreak())
 def sec(i,title):add(P(f'{i}  {title}','h1'))
 def cards(items):
  for i in range(0,len(items),2):
   t=Table([[P(items[i][0],'big'),P(items[i+1][0],'big')],[P(items[i][1],'small'),P(items[i+1][1],'small')]],colWidths=[CW/2]*2)
   t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),pale),('LEFTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,0),6),('BOTTOMPADDING',(0,-1),(-1,-1),8)]));add(t,Spacer(1,7))
 # S1
 cover=MEDIA['cover']
 add(Spacer(1,14),P(CFG['titel'],'title'),P(CFG['untertitel'],'h2'),P('Ergänzung zu Anlage 2 – Auswertestand '+day(cutoff),'h2'),P(MEDIA['continuation']),Spacer(1,7),fit_image(Path(cover['path']),CW,415),Spacer(1,7),P('Datierte Baustellenansicht vom '+cover['caption']+'.','small'))
 for key in ('namen','bezug'):
  if CFG.get(key):add(P(MEDIA['case_ref'] if key=='bezug' else CFG[key],'small'))
 page()
 # S2
 sec(2,'Kernbefunde')
 c=g['tageswerte_ueber'];cards([(str(g['ausgewiesene_tage'])+' Messtage',f'Schallmessung 18.05.–{day(cutoff)}'),(f"{c['60']} von {g['aussentage_hohe_abdeckung']}",'vollständig erfasste Außen-Bautage über 60 dB(A)'),(de(g['hoechster_tages_laeq']['wert_db'])+' dB(A)','höchster Tages-LAeq'),(de(median['laeq_gerundet_db'])+' dB(A)','typischer Bautag, Tagesmedian'),(de(median['abstand_mindestens_db'])+' dB','Abstand zum Ruhetag mindestens'),(str(math.floor(median['energiefaktor_mindestens']))+'-fach','Schallenergie mindestens')])
 add(P(f"Von den {len(high)} Außen-Bautagen mit mindestens 90 % Abdeckung entfallen {len(work)} auf den Südostbalkon (Mo–Fr), {len(nw)} auf Tage mit Messungen am Nordwestbalkon (ein Tag teilweise) und {len(sat)} auf Samstage ({', '.join(day(x['tag']) for x in sat)}). Der Median aller {len(high)} Tage beträgt {de(K['kategorien']['a']['median_db'])} dB(A) (Kapitel 10)."))
 add(P(f"Über alle Außenmesstage an Bautagen mit Tageswert: {K['kategorien']['c']['ueber60']} von {K['kategorien']['c']['anzahl']} über 60 dB(A) (Kapitel 10)."))
 add(P('Vergleichswerte der AVV Baulärm für allgemeine Wohngebiete (tags): Immissionsrichtwert 55 dB(A), Eingreifschwelle 60 dB(A). Nr. 4.1 knüpft Maßnahmen an eine Überschreitung um mehr als 5 dB(A).', 'small'))
 add(P(f"Bei Ansatz des Werts für Gebiete mit gemischter Nutzung (60 dB(A)) lagen {c['65']} von {len(high)} gemessenen Tages-LAeq über 65 dB(A). Die Schwellenvergleiche beziehen sich auf die gemessenen Tages-LAeq. Die AVV Baulärm verwendet einen eigenen Beurteilungspegel.",'small'))
 add(P(f"Typischer Bautag (mittlerer Wert der {len(work)} vollständig erfassten SO-Bautage Mo–Fr, {day(median['tag'])}): {de(median['laeq_gerundet_db'])} dB(A). Einzelvergleich mit der belegten Bauruhe am selben Messort am {day(median['ruhetag'])}: höchstens {de(median['ruhetag_hoechstens_db'])} dB(A). Der Abstand beträgt mindestens {de(median['abstand_mindestens_db'])} dB."))
 part=D['teilerfassung_70_89'];add(P(f"An weiteren {K['kategorien']['b']['anzahl']} Bautagen wurde mit 70–89 % Abdeckung gemessen; ihre rechnerische Untergrenze liegt bei bis zu {de(K['untergrenze_bautag_max_db'])} dB(A)."))
 page()
 # Neues Kapitel 3: nur belegte Bautagebuch-Aussagen.
 sec(3,'Auswirkungen auf die Wohnnutzung')
 add(P('Die Bauarbeiten beeinträchtigen die Nutzung der Wohnung über die gemessenen Pegel hinaus. Beispiele aus dem Bautagebuch:'))
 impacts=[]
 for text,dates in MEDIA['impacts']:
  if text in D['beispieldaten_neu']:
   dates += '; '+D['beispieldaten_neu'][text]
  impacts.append([P(text,'cell'),P(dates,'cell')])
 add(table([[P('Einschränkung','cellhead'),P('Beispiele (Datum)','cellhead')]]+impacts,[350,CW-350]),Spacer(1,10))
 add(P('Arbeitsbeginn vor 7 Uhr (Nachtzeit nach AVV Baulärm)','h2'))
 early=MEDIA['early']+[(day(d),clock,text) for d,clock,text in D['fruehstart_neu']]
 add(table([[P(x,'cellhead') for x in ['Datum','Beginn','Arbeiten']]]+[[P(x,'cell') for x in row] for row in early],[92,68,CW-160]),Spacer(1,11))
 add(KeepTogether([table([[P('Weitere Beeinträchtigungen (nicht Gegenstand der Minderungsquoten)','cellhead')],[P('Sichtbeeinträchtigung und Verschattung, auch durch Gerüste, sowie ein Gerüst am Mietobjekt sind nicht Gegenstand der hier dokumentierten Lärm-, Staub- und Erschütterungsbelastung und werden gesondert geltend gemacht.','cell')]], [CW])]))

 page()
 # S3
 sec(4,'Verlauf der Bauabschnitte')
 w=D['belegdichte_bereinigt'];fig,ax=plt.subplots(2,1,figsize=(8.5,5.3),gridspec_kw={'height_ratios':[1,2]});x=np.arange(len(w));v=[int(q['fotos'])+int(q['videos'])+int(q['nachbarinfos']) for q in w];ax[0].bar(x,v,color='#8ab0b0');ticks=list(range(0,len(w),max(1,len(w)//8)));ax[0].set_xticks(ticks,[f"KW {w[i]['iso_woche'][6:]}/{w[i]['iso_woche'][:4]}" for i in ticks],rotation=35,ha='right',fontsize=6);ax[0].set_ylabel('Belege');ax[0].set_title('Datierte Belege vor der Pegelmessung')
 ax[1].scatter([dt.date.fromisoformat(r['tag']) for r in high],[r['laeq_db'] for r in high],s=14,color='#12666b');ax[1].axhline(55,color='#c49b2c',ls='--',lw=.8);ax[1].axhline(60,color='#b8503d',ls='--',lw=.8);ax[1].set_ylim(45,100);assert all(45<=x['laeq_db']<=100 for x in high);ax[1].set_ylabel('Tages-LAeq dB(A)');ax[1].grid(alpha=.2);import matplotlib.dates as mdates;ax[1].xaxis.set_major_locator(mdates.MonthLocator(interval=1));ax[1].xaxis.set_major_formatter(mdates.DateFormatter('%m/%y'));fig.tight_layout(pad=2.3)
 add(P(f'Die obere Grafik zeigt datierte Belege vor Beginn der Pegelmessung. Die untere zeigt vollständig erfasste Außen-Bautage ab Messbeginn bis {day(cutoff)}.'),plot_image(fig,'verlauf_'+cutoff+'.png',CW))
 rows=[[P(q,'cellhead') for q in ['Abschnitt','Zeitraum','Messtage','Außentage ≥90 %']],[P('Messzeitraum Tiefbau','cell'),P(day('2026-05-18')+'–'+day(cutoff),'cell'),P(len(g['tage']),'cell'),P(len(high),'cell')]]
 add(table(rows,[123,157,94,115]));page()
 # S4
 sec(5,'Gemessener Bautag und Ruhetag')
 fig,ax=plt.subplots(figsize=(8.5,4));axis=np.arange(780)/60+7
 rest_curves=[]
 for s in K['ruhetage']:
  z=series_day(s);a=date_start(s);mins=((z.index.to_numpy()-a)//60).astype(int);levels=z.to_numpy(float)
  e=np.bincount(mins,weights=10**(levels/10),minlength=780);n=np.bincount(mins,minlength=780)
  rest_curves.append(np.where(n>0,10*np.log10(np.maximum(e,1e-30)/np.maximum(n,1)),np.nan))
 with np.errstate(invalid='ignore'):
  rest_energy=np.asarray([10**(q/10) for q in rest_curves])
  rest_n=np.sum(np.isfinite(rest_energy),axis=0)
  rest_e=np.nansum(rest_energy,axis=0)
  rest_y=np.where(rest_n>0,10*np.log10(np.maximum(rest_e,1e-30)/np.maximum(rest_n,1)),np.nan)
 if len(K['ruhetage'])>=2:
  ax.plot(axis,rest_y,label=f"Ruhetag Ø (n = {len(K['ruhetage'])})",color='#687783',lw=1.6,ls='--',zorder=5)
 else:
  s='2026-06-27';z=series_day(s);a=date_start(s);mins=((z.index.to_numpy()-a)//60).astype(int);levels=z.to_numpy(float);e=np.bincount(mins,weights=10**(levels/10),minlength=780);n=np.bincount(mins,minlength=780);y=np.where(n>0,10*np.log10(np.maximum(e,1e-30)/np.maximum(n,1)),np.nan);ax.plot(axis,y,label='Ruhetag 27.06.2026',color='#687783',lw=1.6,ls='--',zorder=5)
 for s,label,color in [(median['tag'],'Median-Bautag '+day(median['tag']),'#12666b'),(median['lautester_bautag'],'Lautester Bautag '+day(median['lautester_bautag']),'#b8503d')]:
  z=series_day(s);a=date_start(s);mins=((z.index.to_numpy()-a)//60).astype(int);levels=z.to_numpy(float);e=np.bincount(mins,weights=10**(levels/10),minlength=780);n=np.bincount(mins,minlength=780);y=np.where(n>0,10*np.log10(np.maximum(e,1e-30)/np.maximum(n,1)),np.nan);ax.plot(axis,y,label=label,color=color,lw=1,zorder=2)
 ax.set(xlim=(7,20),ylim=(30,100),xlabel='Uhrzeit',ylabel='1-Minuten-LAeq dB(A)');ax.set_xticks(range(7,21));ax.set_yticks(range(30,101,10));ax.grid(alpha=.2);ax.legend(fontsize=7,loc='upper right');fig.tight_layout()
 add(P('Die Kurven zeigen gemessene Minutenwerte ohne Untergrenze; fehlende Zeitabschnitte bleiben leer.'),plot_image(fig,'ruhetag_'+cutoff+'.png'),P(f"Median-Bautag {day(median['tag'])}: {de(median['laeq_gerundet_db'])} dB(A). Ruhetag 27.06.2026 einzeln: höchstens {de(median['ruhetag_hoechstens_db'])} dB(A)."))
 if len(K['ruhetage'])>=2:
  add(P(f"Ruhetag Ø aus {len(K['ruhetage'])} Tagen ({', '.join(day(s) for s in K['ruhetage'])}): {de(K['ruhetag_laeq_db'])} dB(A). Für den Vergleichsverlauf gilt eine Mindestabdeckung von 70 %. Kapitel 10 zeigt alle gemessenen Ruhetage mit Abdeckung."))
 page()
 # S5
 sec(6,'Lage und datierte Baustellenansichten')
 add(P('Lageplan: Abstand unter 4 m zur Brandwand und Sichtlinie vom Südostbalkon zu den eingezeichneten Bohrpositionen.'),fit_image(map_target,CW,245),P('Eintragungen des Mieters','small'))
 pics=[(Path(x['path']),x['caption']) for x in MEDIA['pictures']]
 for i,group in enumerate((pics[:2],pics[2:])):
  if not group:continue
  if i:add(PageBreak(),P('6  Lage und datierte Baustellenansichten (Fortsetzung)','h1'))
  row=[]
  for name,caption in group:row.append([fit_image(name,CW/len(group)-8,320),P(caption,'small')])
  add(table([[x[0] for x in row],[x[1] for x in row]],[CW/len(row)]*len(row)),Spacer(1,6))
 page()
 # S6
 sec(7,'Zeitstruktur der Messung')
 rows=[[P('Zeitfenster','cellhead'),P('Median der Tageswerte im Fenster','cellhead')]]
 for q in D['zeitfenster_mediane_so']:
  label=q['fenster'].replace('-', '–')+' Uhr'
  value='höchstens 50 dB(A)' if q['median_db']<50 else de(q['median_db'])+' dB(A)'
  rows.append([P(label,'cell'),P(value,'cell')])
 add(P(f"Basis: dieselben {len(work)} vollständig erfassten SO-Bautage Mo–Fr wie beim Tagesmedian. Je Tag wird der LAeq aus vorhandenen Sekunden des Zeitfensters berechnet; die Tabelle zeigt den Median dieser Tageswerte."),table(rows,[CW/2,CW/2]))
 minute_curves=[]
 for r in work:
  z=series_day(r['tag'],6,21);a=int(pd.Timestamp(r['tag']+' 06:00',tz='Europe/Berlin').timestamp());m=((z.index.to_numpy()-a)//60).astype(int);x=z.to_numpy(float);e=np.bincount(m,weights=10**(x/10),minlength=900);n=np.bincount(m,minlength=900);minute_curves.append(np.where(n>0,10*np.log10(np.maximum(e,1e-30)/np.maximum(n,1)),np.nan))
 import warnings
 with warnings.catch_warnings():
  warnings.simplefilter('ignore',category=RuntimeWarning);y=np.nanmedian(np.asarray(minute_curves),axis=0)
 fig,ax=plt.subplots(figsize=(8,2.6));ax.plot(np.arange(900)/60+6,np.maximum(y,50),color='#12666b',lw=.8);ax.set(xlim=(6,21),ylim=(50,100),xlabel='Uhrzeit',ylabel='Median je Minute, dB(A)');ax.set_xticks(range(6,22));ax.grid(alpha=.2);fig.tight_layout();add(plot_image(fig,'zeitstruktur_'+cutoff+'.png',CW))
 add(P('Die Grafik zeigt den Median der vorhandenen Minutenwerte; Werte unter 50 dB(A) sind an der Unterkante als höchstens 50 dB(A) dargestellt. Fehlende Minuten wurden nicht ergänzt.','small'))
 weather=D['wettervergleich'];add(P(f"Wetterprüfung bis {day(cutoff)} für dieselben {weather['tage']} vollständig erfassten SO-Bautage Mo–Fr. Für {weather['delta_unter_1_db']} von {weather['vergleichbar']} Tagen mit verbleibender Messzeit liegt die absolute Änderung unter 1 dB."))
 if weather['ohne_restwerte']:
  add(P('Nach dem Ausschluss von Zeiten mit Regen und Wind bleiben an folgenden Tagen keine Messsekunden im Tagesfenster: '+', '.join(day(x) for x in weather['ohne_restwerte'])+'.','small'))
 lo=min(z[0] for z in other);hi=max(z[0] for z in other)
 topdiff=max(other,key=lambda z:z[0])[1]['tag']
 above=sum(z['laeq_db']>60 for _,z in other)
 below=sum(z['abdeckung']<.7 for _,z in other)
 add(P(f"Bei den übrigen {len(other)} Tagen beträgt die absolute Änderung {de(lo)} bis {de(hi)} dB. Die größte Abweichung betrifft den {day(topdiff)} ({de(a_by[topdiff]['laeq_db'])} auf {de(b_by[topdiff]['laeq_db'])} dB(A)). An {above} dieser {len(other)} Tage liegt der Pegel der verbleibenden Messzeit über 60 dB(A). Nach dem Wetterfilter liegen {below} der {len(other)} Tage unter 70 % Abdeckung.",'small'));page()
 # S7
 sec(8,'Bauarbeiten vor Beginn der Pegelmessung')
 add(P('Die Übersicht verknüpft datierte Mitteilungen mit eigenen datierten Belegen.'))
 rows=[[P(x,'cellhead') for x in ['Kalenderwoche','Arbeiten lt. Mitteilung des Bauträgers','Eigene Belege']]]
 for q in D['belegwochen']:
  if True:
   yy,ww=q['kalenderwoche'].split('/');label='KW '+ww+'/'+yy
   rows.append([P(label,'cell'),P(q['arbeiten_laut_mitteilung'].replace('Die K2-Tabelle nennt', 'Die Mitteilung des Bauträgers enthält').replace('enthält „','enthält „'),'cell'),P(q['eigene_belege'],'cell')])
 add(table(rows,[74,218,CW-292]));page()
 # S8
 sec(9,'Vollständig erfasste Außen-Bautage')
 add(P('Tages-LAeq aus gemessenen Sekunden, ohne Hochrechnung.'))
 dayrows=[]
 for r in high:
  dayrows.append((day(r['tag'])+(' NW' if r['tag'] in {'2026-06-25','2026-06-26'} else ''),de(r['laeq_db']),de(100*r['abdeckung'],0)+' %'))
 half=(len(dayrows)+1)//2
 def half_table(q):return table([[P(x,'cellhead') for x in ['Datum','LAeq dB(A)','Abdeckung']]]+[[P(x,'cell') for x in row] for row in q],[76,68,66])
 add(Table([[half_table(dayrows[:half]),half_table(dayrows[half:])]],colWidths=[CW/2,CW/2],style=[('VALIGN',(0,0),(-1,-1),'TOP')]))
 add(P('NW: 25.06.2026 zeitweise, 26.06.2026 ganztägig Nordwestbalkon.','small'),Spacer(1,8),P('Weitere Messtage mit 70–89 % Abdeckung','h2'))
 for q in top:add(P(f"{day(q['tag'])}: {de(q['abdeckung_prozent'],0)} % Abdeckung; Untergrenze {de(q['untergrenze_13h_db'])} dB(A).",'small'))
 add(P('Die rechnerische Untergrenze setzt Lücken mit Nullenergie an.','small'))
 aug_lb=lowaug['laeq_db']+10*math.log10(lowaug['abdeckung'])
 add(P(f"Zwischen dem 06.08. und dem 02.09. ist kein vollständig erfasster Außen-Bautag ausgewiesen. Am {day(lowaug['tag'])} wurden {de(lowaug['abdeckung']*100,0)} % des Tagesfensters erfasst (rechnerische Untergrenze {de(aug_lb)} dB(A)).",'small'));page()
 # S9
 sec(10,'Alle Messtage nach Abdeckung')
 add(P('Tages-LAeq aus gemessenen Sekunden von 07:00 bis 20:00 Uhr; fehlende Zeit wurde nicht ergänzt.'))
 kr=K['kategorien']
 headers=['Gruppe','Anzahl','über 55 dB(A)','über 60 dB(A)','über 65 dB(A)','Median']
 rows=[[P(x,'cellhead') for x in headers]]
 labels=[('a','a) Außen, Bautag, ≥90 %'),('b','b) Außen, Bautag, 70–89 %'),('c','c) Summe a+b: Tage mit Tageswert'),('d','d) Außen, Bautag, <70 %: Messfenster, kein Tageswert')]
 for key,label in labels:
  v=kr[key]
  cells=[label,str(v['anzahl'])]+(['–']*4 if key=='d' else [str(v['ueber55']),str(v['ueber60']),str(v['ueber65']),de(v['median_db'])+' dB(A)'])
  rows.append([P(x,'cell') for x in cells])
 add(table(rows,[198,47,68,68,68,CW-449]),Spacer(1,8))
 add(P('Innenraummessungen: '+str(kr['Innenraummessung']['anzahl'])+' ('+', '.join(day(s) for s in kr['Innenraummessung']['tage'])+').','small'))
 unknown=kr['Messort nicht belegt']
 if unknown['anzahl']:
  add(P('PCE-Aufzeichnungen ohne belegten Messort: '+', '.join(day(x) for x in unknown['tage'])+'. Sie zählen ausschließlich zur Gesamtzahl der Messtage.','small'))
 if K['kurzaufzeichnungen']:
  add(P('Kurzaufzeichnungen unter 60 s im Tagesfenster: '+', '.join(day(x['tag'])+f" ({x['sekunden']} s)" for x in K['kurzaufzeichnungen'])+'. Diese zählen nicht als ausgewiesene Messtage.','small'))
 add(P('Gemessene Ruhetage','h2'))
 rest_rows=[[P(x,'cellhead') for x in ('Datum / Einordnung','Abdeckung','LAeq der Messzeit','Im Vergleichsverlauf')]]
 for item in sorted([a_by[t] for t in kr['Ruhetag']['tage']]+[z for z in K['kurzaufzeichnungen'] if z['kategorie']=='Ruhetag'],key=lambda x:x['tag']):
  x=item;tag=x['tag']
  grund='Bauruhe laut Messprotokoll' if tag=='2026-06-27' else K['ruhetag_begruendung_und_beleg'].get(tag,{'grund':'Sonntag'})['grund']
  rest_rows.append([P(day(tag)+' – '+grund,'cell'),P(de(x['abdeckung']*100,2 if x['sekunden']<60 else 1)+' %','cell'),P(de(x['laeq_db'])+' dB(A)','cell'),P('Ja' if tag in K['ruhetage'] else ('Nein (Kurzaufzeichnung)' if x['sekunden']<60 else 'Nein (<70 %)'),'cell')])
 add(table(rest_rows,[238,71,102,CW-411]),Spacer(1,6))

 add(P('Einbezogen sind alle Außenmessungen an Bautagen (Montag bis Samstag außer Feiertage und belegte Bauruhe). Nicht einbezogen sind Innenraummessungen, da die AVV Baulärm außen vor dem Fenster misst, sowie Ruhetage. Eine Auswahl nach Pegel findet nicht statt.'))
 add(P('Außen-Messtage an Bautagen je Kalenderwoche','h2'))
 weeks=K['wochen_aussen_bautag']
 wr=[[P(x,'cellhead') for x in ['KW','Anzahl','KW','Anzahl']]]
 keys=sorted(int(k) for k in weeks);half=(len(keys)+1)//2
 for i in range(half):
  a=keys[i];b=keys[i+half] if i+half<len(keys) else None
  wr.append([P(str(a),'cell'),P(str(weeks[str(a)]),'cell'),P(str(b) if b else '','cell'),P(str(weeks[str(b)]) if b else '','cell')])
 add(table(wr,[CW/4]*4),P('KW 21 und 22: nur Innenraummessungen (siehe oben).','small'),P('Gemessene Samstage ohne belegte Bauruhe (als Bautag gewertet): '+'; '.join(day(q['tag'])+': '+de(q['laeq_db'])+' dB(A)'+(f" ({de(a_by[q['tag']]['abdeckung']*100,1)} % Abdeckung, Gruppe d) → Messfenster)" if q['tag'] in kr['d']['tage'] else '') for q in K['samstage_bautaetigkeit'])+'.','small'))
 if K['alle_wochen_gemessen']:add(P('In jeder Kalenderwoche wurde gemessen.','small'))
 page()
 # S10
 sec(11,'Messverfahren und Datensicherung')
 add(P('Messort Südostbalkon; am 25. und 26.06.2026 zeitweise bzw. ganztägig Nordwestbalkon. Das PCE-323 stand auf einem Stativ in 1,40 m Höhe mit Windschirm. Kalibrator PCE-SC 43, 94 dB(A); Schallpegelmessgerät Klasse 2 nach IEC 61672-1.'))
 add(P(f"Für {g['ausgewiesene_tage']} ausgewiesene Messtage liegen {num(g['gemessene_tagsekunden'])} Tagsekunden des Messgeräts vor. Werte über 100 dB(A) sind für Kennwerte auf 100 dB(A) begrenzt. Es wurden keine Zeitlücken ergänzt."))
 media=D['medien_stichtag']
 add(P(f"Bis zum Auswertestand gehören zur Gesamtdokumentation {media['fotos']} datierte Fotos und {media['videos']} Videos, davon {media['schallmessvideos']} Schallmessvideos, sowie {media['kalibrierfotos']} verschiedene Kalibrierfotos."))
 add(P('Für den Ruhetagswert in Kapitel 2 werden erfasste Werte unter 50 dB(A) rechnerisch auf 50 dB(A) angehoben. Diese konservative Annahme erhöht den Vergleichswert und verkleinert den ausgewiesenen Abstand. Die Kurven in Kapitel 5 zeigen die gemessenen Werte ohne Untergrenze.'))
 safety=g['sicherheitsrechnung_ueber']
 add(P(f"Nach einer gesonderten Sicherheitsrechnung mit 3,0 dB Abzug für Reflexion und 1,4 dB für das Gerät, beide vorsorglich in derselben Richtung angesetzt, verbleiben {safety['55']} von {g['aussentage_hohe_abdeckung']} vollständig erfassten Außen-Bautagen über dem Immissionsrichtwert von 55 dB(A) und {safety['60']} über 60 dB(A)."))
 def footer(c,doc):
  c.saveState();c.setFont('Base',7);c.setFillColor(muted);c.drawString(M,23,'Ergänzung zu Anlage 2 – Auswertestand '+day(cutoff));c.drawRightString(W-M,23,str(doc.page));c.restoreState()
 path=OUT/('Belastungsdokumentation_Ergaenzung_Anlage_2_bis_'+cutoff+'.pdf')
 if path.exists():raise FileExistsError(path)
 doc=SimpleDocTemplate(str(path),pagesize=A4,leftMargin=M,rightMargin=M,topMargin=40,bottomMargin=42,title='Belastungsdokumentation',author='');doc.build(story,onFirstPage=footer,onLaterPages=footer)
 return dict(datei=path.name,bytes=path.stat().st_size)
if __name__=='__main__':print(json.dumps([render(args.stand)],ensure_ascii=False))

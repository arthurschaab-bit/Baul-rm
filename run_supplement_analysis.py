from pathlib import Path
from collections import Counter,defaultdict
import csv,json,datetime as dt,math,re,hashlib
import numpy as np,pandas as pd,openpyxl
from baul_rm.supplement import read_daily_pce,day_values,classify_day,early_start,energetic_mean,second_at
from baul_rm.meteo_dwd import read_processed
from baul_rm.meteo_analysis import MeteoConfig,events,exclusion_ranges,interval_mask
import argparse

parser=argparse.ArgumentParser(description="Mess- und Wetterdaten für die Ergänzung fortschreiben.")
parser.add_argument("--config",type=Path,required=True)
cfg=json.loads(parser.parse_args().config.read_text(encoding="utf-8-sig"))
R=Path(cfg["output"])
PRE=Path(cfg["predecessor"])
OLD=Path(cfg["signal_base"])
F=Path(cfg["app_exports"])
BAU=Path(cfg["site_sources"])
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):(R/p).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
D=load(PRE/'zahlen_bis_2026-11-04.json');K=load(PRE/'klassifikation.json')
prevD=load(PRE/'zahlen_bis_2026-11-04.json')
END=load(R/'wetterstand.json')['end']
frame=pd.read_pickle(OLD/'stage_mit_C_A_cal'/'report_data.pkl')[['leq','maximum','source']]
frame=frame.loc[frame.index<second_at(cfg['base_end_exclusive'],'00:00')]
excluded=[]
for date,time,n in cfg["calibration_exclusions"]:
 a=second_at(date,time);excluded.extend(range(a,a+n))
frame=frame.loc[~frame.index.isin(excluded)]
so=set(cfg['confirmed_so_days'])
inputs=[]
short=[]
new={}
for timestamp in pd.date_range(cfg['extension_start'],cfg['intake_through']):
 day=str(timestamp.date());p=list((F/day).rglob('laermprotokoll_'+day+'.csv'))
 if not p:
  inputs.append(dict(tag=day,status='kein Tagesexport'));continue
 assert len(p)==1
 z=read_daily_pce(p[0],day)
 if day<cfg['base_end_exclusive']:
  beg=second_at(day,'00:00');stop=second_at(str(timestamp.date()+dt.timedelta(days=1)),'00:00')
  z=frame.loc[(frame.index>=beg)&(frame.index<stop)]
 loc='aussen' if day<cfg['base_end_exclusive'] or day in so else 'nicht belegt'
 x=day_values(z,day,loc)
 inputs.append(dict(tag=day,status='eingelesen',sha256=sha(p[0]),**{k:v for k,v in x.items() if k!='tag'}))
 if day<=END:new[day]=(z,x)
frame=pd.concat([frame]+[z for day,(z,x) in new.items() if day>=cfg['base_end_exclusive']]).sort_index()
frame=frame.loc[frame.index<second_at(str(dt.date.fromisoformat(END)+dt.timedelta(days=1)),'00:00')]
assert frame.index.is_unique and frame.leq.max()<=100
frame.to_pickle(R/'signal.pkl')
wold=read_processed(OLD/'pruefdossier'/'meteo_merged_through04.csv')
wn=read_processed(R/'dwd'/'processed'/'meteo_10min.csv')
w=pd.concat([wold,wn.loc[wn.index>wold.index.max()]]).sort_index()
assert w.index.is_unique and (w.index.to_series().diff().dropna()==pd.Timedelta(minutes=10)).all()
config=MeteoConfig(**load(OLD/'stage_mit_C_A_cal'/'report_audit.json')['audit']['meteo']['config'])
ev=events(w,config);ranges=exclusion_ranges(ev,config)
A=D['varianten']['A']['tage'];B=D['varianten']['B']['tage']
Bby={x['tag']:x for x in B}
cats={tag:key for key in ('a','b','d','Ruhetag','Innenraummessung') for tag in K['kategorien'][key]['tage']}
for day,(z,x) in new.items():
 key=classify_day(day,x['messort_art'],x['sekunden'],set(cfg['holidays']),cfg['saturday_rest'])
 if x['sekunden']<60:
  short.append(dict(x,kategorie=key))
  continue
 cats[day]=key;A.append(x)
 if key=='Ruhetag':
  if day in cfg['saturday_rest']:
   K['ruhetag_begruendung_und_beleg'][day]={'grund':'Belegte Bauruhe','beleg':cfg['saturday_rest'][day]}
  else:
   K['ruhetag_begruendung_und_beleg'][day]={'grund':'Feiertag' if day in cfg['holidays'] else 'Sonntag','beleg':'Kalender'}
 if dt.date.fromisoformat(day).weekday()==5 and key!='Ruhetag':
  K['samstage_bautaetigkeit'].append(dict(tag=day,laeq_db=x['laeq_db'],beleg='Bauruhe nicht belegt'))
 bz=z.loc[~interval_mask(z.index,ranges)]
 bx=day_values(bz,day,x['messort_art'])
 if bx['laeq_db'] is not None:B.append(bx);Bby[day]=bx
groups=defaultdict(list)
for x in A:groups[cats[x['tag']]].append(x)
def metrics(items):
 levels=[x['laeq_db'] for x in items]
 return dict(anzahl=len(items),tage=[x['tag'] for x in items],ueber55=sum(x>55 for x in levels),ueber60=sum(x>60 for x in levels),ueber65=sum(x>65 for x in levels),median_db=float(np.median(levels)) if levels else None)
K['kategorien']={key:metrics(groups[key]) for key in ('a','b','d','Ruhetag','Innenraummessung','Messort nicht belegt')}
K['kategorien']['c']=metrics(groups['a']+groups['b'])
K['kurzaufzeichnungen']=short
def eligible(x):
 return dt.date.fromisoformat(x['tag']).weekday()<5 and x['tag'] not in ('2026-06-25','2026-06-26')
def sec(day,a='07:00',b='20:00'):
 return frame.loc[(frame.index>=second_at(day,a))&(frame.index<second_at(day,b)),'leq']
def lb(x):return x['laeq_db']+10*math.log10(x['abdeckung'])
def get_stats(items,high,partial,rest):
 w=sorted((x for x in high if eligible(x)),key=lambda x:(x['laeq_db'],x['tag']))
 mid=w[(len(w)-1)//2];hi=max(high,key=lambda x:x['laeq_db'])
 ref=math.ceil(energetic_mean(sec(rest).clip(lower=50))*10)/10
 gap=math.floor((mid['laeq_db']-ref)*10)/10
 return dict(messtage=len(items),sekunden=sum(x['sekunden'] for x in items),hoch=len(high),so_mofr=len(w),
    ueber60=sum(x['laeq_db']>60 for x in high),ueber65=sum(x['laeq_db']>65 for x in high),
    tageswert=len(high)+len(partial),tageswert_ueber60=sum(x['laeq_db']>60 for x in high+partial),
    max_db=hi['laeq_db'],max_tag=hi['tag'],median_db=mid['laeq_db'],median_tag=mid['tag'],
    median_alle_db=metrics(high)['median_db'],ruhetag=rest,ruhetag_ceiling=ref,abstand=gap,faktor=math.floor(10**(gap/10)),
    teil=len(partial),untergrenze=max(map(lb,partial)),sicherheit55=sum(x['laeq_db']-4.4>55 for x in high),sicherheit60=sum(x['laeq_db']-4.4>60 for x in high))
stats=get_stats(A,groups['a'],groups['b'],'2026-06-27')
sep=get_stats(*[[x for x in g if x['tag']<='2026-09-30'] for g in (A,groups['a'],groups['b'])],'2026-06-27')
october=get_stats(*[[x for x in g if x['tag']>='2026-10-01'] for g in (A,groups['a'],groups['b'])],'2026-10-25')
g=D['varianten']['A'];g.update(ausgewiesene_tage=stats['messtage'],gemessene_tagsekunden=stats['sekunden'],aussentage_hohe_abdeckung=stats['hoch'],tageswerte_ueber={str(s):sum(x['laeq_db']>s for x in groups['a']) for s in (55,60,65)},sicherheitsrechnung_ueber={'55':stats['sicherheit55'],'60':stats['sicherheit60']},hoechster_tages_laeq={'tag':stats['max_tag'],'wert_db':stats['max_db']})
D['median_bautag'].update(tag=stats['median_tag'],laeq_db=stats['median_db'],laeq_gerundet_db=round(stats['median_db'],1),anzahl_werktage=stats['so_mofr'],abstand_mindestens_db=stats['abstand'],energiefaktor_mindestens=10**(stats['abstand']/10),lautester_bautag=stats['max_tag'],ruhetag_hoechstens_db=stats['ruhetag_ceiling'])
K['untergrenze_bautag_max_db']=stats['untergrenze']
D['untergrenzen_top5']=[dict(tag=x['tag'],abdeckung_prozent=100*x['abdeckung'],untergrenze_13h_db=lb(x)) for x in sorted(groups['b'],key=lb,reverse=True)[:5]]
work=[x for x in groups['a'] if eligible(x)]
D['zeitfenster_mediane_so']=[dict(fenster=f'{a}-{b}',median_db=float(np.median([m for x in work if (m:=energetic_mean(sec(x['tag'],a+':00',b+':00'))) is not None]))) for a,b in [('07','12'),('12','13'),('13','17'),('17','20')]]
deltas={x['tag']:abs(x['laeq_db']-Bby[x['tag']]['laeq_db']) for x in work if x['tag'] in Bby}
D['wettervergleich']=dict(tage=len(work),vergleichbar=len(deltas),ohne_restwerte=[x['tag'] for x in work if x['tag'] not in Bby],delta_unter_1_db=sum(z<1 for z in deltas.values()),bis=END)
K['ruhetage']=[x['tag'] for x in groups['Ruhetag'] if x['abdeckung']>=.7]
K['ruhetag_laeq_db']=energetic_mean(pd.concat([sec(x) for x in K['ruhetage']]))
K['wochen_aussen_bautag']={str(i):sum(dt.date.fromisoformat(x['tag']).isocalendar().week==i for key in ('a','b','d') for x in groups[key]) for i in range(21,dt.date.fromisoformat(END).isocalendar().week+1)}
K['alle_wochen_gemessen']=all(K['wochen_aussen_bautag'].values())
K['datenstand']=D['datenstand']=D['auswertestand']=END
# Earlier deduplicated inventory plus currently dated originals in the same roots.
inventory=Path(cfg['prior_media_inventory'])
roots={'Baustelle':BAU/'Fotos_Videos','Laermprotokoll':F}
def capture(p):
 for part in reversed(p.parts):
  for m in re.finditer(r'(202[56])[-_]?([01]\d)[-_]?([0-3]\d)|(?<!\d)([0-3]\d)\.([01]\d)\.(202[56])',part):
   y,mo,da=m.groups()[:3] if m[1] else (m[6],m[5],m[4])
   try:return dt.date(int(y),int(mo),int(da)).isoformat()
   except ValueError:pass
classes={};known=set();sizes=defaultdict(list);identified={}
with inventory.open(encoding='utf-8-sig') as fh:
 for x in csv.DictReader(fh,delimiter=';'):
  p=Path(x['relativer_pfad']);key=(x['wurzel'],str(p).casefold())
  known.add(key)
  if x['zaehlklasse'].startswith('ausgeschlossen'):continue
  ident=x['sha256_wenn_noetig'] or key
  d=capture(p);assert d
  sizes[int(x['groesse_bytes'])].append((ident,roots[x['wurzel']]/p))
  if d<=END:
   classes[ident]=x['zaehlklasse'];identified[ident]=min(d,identified.get(ident,d))
add_media=[]
for label,root in roots.items():
 directories=[root] if label=='Baustelle' else [q for q in root.iterdir() if q.is_dir() and '2026-11-05'<=q.name<=END]
 for scan in directories:
  for p in scan.rglob('*'):
   ext=p.suffix.lower()
   if ext not in {'.jpg','.jpeg','.heic','.mp4','.mov','.m4v','.avi','.mkv'} or not p.is_file():continue
   rel=p.relative_to(root);key=(label,str(rel).casefold());d=capture(rel)
   if key in known or not d or not '2026-11-01'<=d<=END or '_frames_for_ai' in str(rel).lower():continue
   hashnew=sha(p)
   ident=hashnew
   for old,path in sizes[p.stat().st_size]:
    if hashnew==(old if isinstance(old,str) else sha(path) if path.exists() else ''):
     ident=old;break
   sizes[p.stat().st_size].append((ident,p))
   kind=('Kalibrierfoto' if 'kalibrier' in str(rel).casefold() else 'Foto') if ext in {'.jpg','.jpeg','.heic'} else ('Schallmessvideo' if 'schallmessvideos' in str(rel).casefold() or re.match(r'video_\d{8}_',p.name) else 'Video')
   if ident not in classes:
    classes[ident]=kind;identified[ident]=d;add_media.append(dict(tag=d,datei=p.name,sha256=hashnew,art=kind))
def media(start,end):
 c=Counter(kind for k,kind in classes.items() if start<=identified[k]<=end)
 return dict(fotos=c['Foto'],videos=c['Video']+c['Schallmessvideo'],schallmessvideos=c['Schallmessvideo'],kalibrierfotos=c['Kalibrierfoto'])
D['medien_stichtag']=media('1900',END)
sep['medien']=media('1900','2026-09-30');october['medien']=media('2026-10-01',END);stats['medien']=D['medien_stichtag']
wb=Path(cfg['diary'])
diary={}
for rownum,row in enumerate(openpyxl.load_workbook(wb,read_only=True,data_only=True).worksheets[0].values,1):
 cells=[str(x) for x in row if x is not None]
 if not cells or not re.fullmatch(r'\d\d\.\d\d\.2026',cells[0]):continue
 day=dt.datetime.strptime(cells[0],'%d.%m.%Y').date().isoformat()
 if '2026-10-01'<=day<=END:diary[day]=(rownum,cells)
early=[]
observations=[]
for day,(line,cells) in diary.items():
 text=' '.join(cells[4:])
 for m in list(re.finditer(r'(Baggergeräusche|Hämmern \([^)]*\)|Presslufthammer)[^.!;]{0,65}?(?:ca\.|gegen) 06:(\d\d)',text))[:1]:
  mi=int(m[2]); match=early_start(frame,day,mi)
  observations.append(dict(tag=day,zeile=line,tagebuch=f'06:{mi:02}',art=m[1],**match))
  if match['minute'] is not None:
   clock=f"06:{match['minute']:02}";early.append((day,clock,m[1]+'. Beginn laut Bautagebuch ca. '+f'06:{mi:02}'+' Uhr, Pegelanstieg um '+clock+' Uhr.'))
D['fruehstart_neu']=early
D['beispieldaten_neu']=cfg['additional_diary_examples']
write('zahlen_bis_'+END+'.json',D);write('klassifikation.json',K)
write('Pruefdaten.json',dict(stand=END,alt04=load(PRE/'pruefnachweis_daten.json')['neu'],alt04_sicherheit=prevD['varianten']['A']['sicherheitsrechnung_ueber'],alt04_medien=prevD['medien_stichtag'],alt04_sekunden=prevD['varianten']['A']['gemessene_tagsekunden'],sep=sep,oktober=october,neu=stats,wetter_delta=deltas,inputs=inputs,fruehstarts=observations,medien_neu=add_media,diary_letzter_tag=max(diary),kurzaufzeichnungen=short))
print(json.dumps(dict(stand=END,stats=stats,weather=D['wettervergleich'],early=observations,short=short,unlocated=K['kategorien']['Messort nicht belegt']['tage'],media_new=len(add_media)),ensure_ascii=False))

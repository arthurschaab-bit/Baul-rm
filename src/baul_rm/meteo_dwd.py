"""DWD CDC observations: immutable downloads, metadata driven columns, UTC intervals.

No location of the private measurement is accepted or persisted by this module.
The requested period must start in 2000 or later (older CDC timestamps use MEZ).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

import numpy as np
import pandas as pd

BASE = 'https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/'
ATTRIBUTION = 'Datenbasis: Deutscher Wetterdienst; CC BY 4.0'
TZ = 'Europe/Berlin'
# Select roles from the downloaded parameter description and unit, not a fixed
# list of CSV column identifiers. Unknown/ambiguous schemas fail explicitly.
ROLES = {
    'precipitation': {'rain_mm': ('Summe.*Niederschlag', {'mm'}),
                      'rain_duration_min': ('Niederschlagsdauer', {'minute','minuten','min'}),
                      'rain_indicator': ('Niederschlagsindikator', {'numerischer code','numerical code'})},
    'wind': {'wind_mean_ms': ('Windgeschwindigkeitsmittel|mittlere Windgeschwindigkeit', {'m/sec','m/s'}),
             'wind_direction_deg': ('Windrichtungsmittel|mittlere Windrichtung', {'grad','°'})},
    'extreme_wind': {'gust_ms': ('^Maximum der Windgeschwindigkeit', {'m/sec','m/s'})},
}


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def read_text_table(payload):
    """CDC station metadata include non-tabular legend/footer lines."""
    text=payload.decode('utf-8-sig',errors='strict') if payload.startswith(b'\xef\xbb\xbf') else payload.decode('cp1252')
    reader=csv.DictReader(io.StringIO(text),delimiter=';')
    return [{str(k).strip():str(v or '').strip() for k,v in row.items() if k is not None}
            for row in reader if re.fullmatch(r'\s*\d+\s*',next(iter(row.values())) or '')]


def schema_from_metadata(payload, product):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name=next(n for n in archive.namelist() if n.startswith('Metadaten_Parameter_') and n.endswith('.txt'))
        rows=read_text_table(archive.read(name))
    schema={}
    for role,(pattern,units) in ROLES[product].items():
        candidates={r['Parameter']:(r['Einheit'],r['Parameterbeschreibung']) for r in rows
                    if re.search(pattern,r['Parameterbeschreibung'],re.I) and r['Einheit'].lower() in units}
        if len(candidates)!=1:
            raise ValueError(f'Ambiguous or missing metadata for {product}/{role}: {candidates}')
        column,(unit,description)=next(iter(candidates.items()))
        schema[role]={'column':column,'unit':unit,'description':description}
    return schema


def station_metadata(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names=archive.namelist()
        geography=read_text_table(archive.read(next(n for n in names if n.startswith('Metadaten_Geographie_') and n.endswith('.txt'))))
        devices={n:read_text_table(archive.read(n)) for n in names if n.startswith('Metadaten_Geraete_') and n.endswith('.txt')}
    return {'geography':geography,'devices':devices}


def normalize_archive(payload,schema,product,station,kind):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names=[n for n in archive.namelist() if n.startswith('produkt_') and n.endswith('.txt')]
        if len(names)!=1:raise ValueError('Expected one DWD product table')
        frame=pd.read_csv(io.BytesIO(archive.read(names[0])),sep=';',encoding='cp1252')
    frame.columns=frame.columns.str.strip()
    stamps=pd.to_datetime(frame['MESS_DATUM'].astype(str),format='%Y%m%d%H%M',utc=True,errors='raise')
    if (stamps.dt.year<2000).any():
        frame=frame.loc[stamps.dt.year>=2000];stamps=stamps.loc[stamps.dt.year>=2000]
    out=pd.DataFrame(index=pd.DatetimeIndex(stamps).tz_convert(TZ))
    if not out.index.is_unique:raise ValueError('Duplicate timestamp within DWD archive')
    for role,meta in schema.items():
        out[role]=pd.to_numeric(frame[meta['column']],errors='raise').replace(-999,np.nan).to_numpy()
    out[product+'_qn']=pd.to_numeric(frame['QN'],errors='raise').replace(-999,np.nan).to_numpy()
    out[product+'_station']=str(station).zfill(5)
    out[product+'_status']='qualitätsgeprüft' if kind=='historical' else 'vorläufig, Qualitätsprüfung nicht abgeschlossen'
    if kind=='now':out[product+'_status']='vorläufig, nicht qualitätsgeprüft (now)'
    out[product+'_origin']=names[0]
    return out.sort_index()


def haversine_km(lat1,lon1,lat2,lon2):
    a,b=map(math.radians,[lat1,lat2]);x=math.radians(lat2-lat1);y=math.radians(lon2-lon1)
    return 6371.0088*2*math.asin(math.sqrt(math.sin(x/2)**2+math.cos(a)*math.cos(b)*math.sin(y/2)**2))


class DownloadCache:
    """One fetch directory, exclusive writes, SHA verification on reuse, TLS verification."""
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.fetched_at=dt.datetime.now(dt.timezone.utc).isoformat()
        self.directory=self.root/'raw'/(dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
        self.directory.mkdir(parents=True,exist_ok=False)
        self.rows=[];self.memory={}

    def get(self,url):
        if url in self.memory:return self.memory[url]
        if not url.startswith(BASE):raise ValueError('Only official HTTPS DWD observations allowed')
        destination=self.directory/(f'{len(self.rows):03d}_'+(Path(urllib.parse.urlparse(url).path).name or 'index.html'))
        error=None
        for attempt in range(3):
            try:
                try:
                    with urllib.request.urlopen(url,timeout=60) as response:
                        status=response.status;payload=response.read()
                except urllib.error.URLError as exc:
                    if os.name!='nt' or not isinstance(exc.reason,ssl.SSLCertVerificationError):raise
                    # Windows trust store can contain the enterprise CA that the
                    # Python bundle lacks. Never disable certificate validation.
                    script="$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing -Uri '"+url.replace("'","''")+"' -OutFile '"+str(destination).replace("'","''")+"'"
                    subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],check=True,capture_output=True,timeout=90)
                    payload=destination.read_bytes();destination.unlink();status=200
                if not payload:raise ValueError('Empty DWD response')
                with destination.open('xb') as handle:handle.write(payload)
                self.rows.append({'file':destination.relative_to(self.root).as_posix(),'url':url,
                                  'fetched_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                                  'http_status':status,'bytes':len(payload),'sha256':sha256(payload),
                                  'attribution':ATTRIBUTION})
                self.memory[url]=payload;self.write_manifest();return payload
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                error=exc
                if attempt<2:time.sleep(2**attempt)
        raise RuntimeError(f'DWD download failed after three attempts: {url}') from error

    def write_manifest(self):
        path=self.root/'manifest.csv'
        previous=pd.read_csv(path).to_dict('records') if path.exists() else []
        names={r['file'] for r in self.rows}
        previous=[r for r in previous if r['file'] not in names]
        pd.DataFrame(previous+self.rows).to_csv(path,index=False)

    def links(self,url):
        return [urllib.parse.urljoin(url,x) for x in re.findall(r'href="([^"]+)"',self.get(url).decode('utf-8')) if x!='../']


def _station_frame(cache,product,station,begin,end):
    base=BASE+'10_minutes/'+product+'/'
    for url in cache.links(base):
        if 'BESCHREIBUNG' in url and url.endswith('.pdf'):cache.get(url)
    meta_links=cache.links(base+'meta_data/')
    meta_urls=[u for u in meta_links if re.search('_'+station+r'\.zip$',u)]
    if not meta_urls:return None,None,None
    metadata=cache.get(meta_urls[0]);schema=schema_from_metadata(metadata,product)
    pieces=[]
    # Read catalogues for both archives; download only files overlapping the
    # interval. Historical records win where archive classes overlap.
    for kind in ('historical','recent','now'):
        if kind=='now' and end<pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=2):continue
        for url in cache.links(base+kind+'/'):
            if not re.search('_'+station+r'_',url) or not url.endswith('.zip'):continue
            match=re.search(r'_(\d{8})_(\d{8})_hist.zip$',url)
            if match and (pd.Timestamp(match[2],tz='UTC')+pd.Timedelta(days=1)<begin or pd.Timestamp(match[1],tz='UTC')>end):continue
            frame=normalize_archive(cache.get(url),schema,product,station,kind)
            frame=frame.loc[(frame.index>begin)&(frame.index<=end)]
            if len(frame):pieces.append(frame)
    if not pieces:return None,metadata,schema
    result=pd.concat(pieces);result=result.loc[~result.index.duplicated(keep='first')].sort_index()
    return result,metadata,schema


def _candidates(cache,product,primary_meta):
    url=BASE+'10_minutes/'+product+'/recent/'
    lists=[u for u in cache.links(url) if u.endswith('Beschreibung_Stationen.txt')]
    if len(lists)!=1:raise ValueError('Missing DWD station catalogue')
    geo=station_metadata(primary_meta)['geography'][-1]
    candidates=[]
    for line in cache.get(lists[0]).decode('cp1252').splitlines():
        parts=line.split()
        if len(parts)<7 or not parts[0].isdigit():continue
        try:
            lat,lon=float(parts[4]),float(parts[5])
            distance=haversine_km(float(geo['Geogr.Breite']),float(geo['Geogr.Laenge']),lat,lon)
            candidates.append((distance,parts[0].zfill(5)))
        except ValueError:continue
    return sorted(candidates)


def fetch_weather(root,start,end,station='03379',refresh=False):
    """Fetch complete interval plus margins for events/buffers and daily crosscheck.

    A cached, hash-verified snapshot is reused unless refresh is explicit. Failed
    downloads or unresolved essential weather values never replace processed data.
    """
    root=Path(root);processed=root/'processed';state=processed/'fetch.json'
    begin=pd.Timestamp(start,tz=TZ)-pd.Timedelta(days=2)
    finish=pd.Timestamp(end,tz=TZ)+pd.Timedelta(days=3)
    if begin.year<2000:raise ValueError('Supported UTC observations start in 2000')
    if state.exists() and not refresh:
        saved=json.loads(state.read_text(encoding='utf-8'))
        if saved['start']==start and saved['end']==end and saved['station']==station:
            for row in pd.read_csv(root/'manifest.csv').to_dict('records'):
                if sha256((root/row['file']).read_bytes())!=row['sha256']:raise ValueError('Weather raw hash mismatch')
            if sha256((processed/'meteo_10min.csv').read_bytes())!=saved['processed_sha256']:raise ValueError('Processed weather hash mismatch')
            if saved.get('audit_sha256')!=sha256((processed/'audit.json').read_bytes()):raise ValueError('Weather audit cache mismatch; refresh required')
            return saved
    cache=DownloadCache(root);index=pd.date_range(begin+pd.Timedelta(minutes=10),finish,freq='10min')
    result=pd.DataFrame(index=index);audit={'substitutions':[],'schemas':{},'stations':{},'missing':{}}
    for product in ROLES:
        frame,metadata,schema=_station_frame(cache,product,station,begin,finish)
        if metadata is None:raise ValueError('Primary station metadata unavailable')
        audit['schemas'][product]=schema;audit['stations'][product+'_'+station]=station_metadata(metadata)
        if frame is None:frame=pd.DataFrame(index=index,columns=[*schema,product+'_qn',product+'_station',product+'_status',product+'_origin'])
        frame=frame.reindex(index)
        essential=[r for r in schema if r!='wind_direction_deg' and r!='rain_indicator']
        missing=frame[essential].isna().any(axis=1)
        if missing.any():
            for distance,alternative in _candidates(cache,product,metadata):
                if alternative==station:continue
                other,other_meta,_=_station_frame(cache,product,alternative,begin,finish)
                if other is None:continue
                other=other.reindex(index)
                usable=missing & other[essential].notna().all(axis=1)
                if usable.any():
                    frame.loc[usable]=other.loc[usable]
                    audit['stations'][product+'_'+alternative]=station_metadata(other_meta)
                    audit['substitutions'].append({'product':product,'station':alternative,'distance_to_primary_km':distance,
                       'timestamps':[x.isoformat() for x in index[usable]],'reason':'Primary station missing parameter/interval'})
                missing=frame[essential].isna().any(axis=1)
                if not missing.any():break
        audit['missing'][product]=int(missing.sum())
        if missing.any():raise ValueError(f'Incomplete weather {product}: {int(missing.sum())} intervals; no complete observation substitute')
        result=result.join(frame)
    # End timestamp belongs to the PRECEDING ten minutes, represented [start,end).
    result.index.name='interval_end_berlin'
    result.insert(0,'interval_start_berlin',result.index-pd.Timedelta(minutes=10))
    old=pd.read_csv(processed/'meteo_10min.csv') if (processed/'meteo_10min.csv').exists() else None
    processed.mkdir(parents=True,exist_ok=True)
    candidate=processed/'meteo_10min.next.csv';result.to_csv(candidate,index=True)
    changes=[]
    if old is not None:
        newer=pd.read_csv(candidate);common=old.merge(newer,on='interval_end_berlin',suffixes=('_old','_new'))
        for column in [*ROLES['precipitation'],*ROLES['wind'],*ROLES['extreme_wind']]:
            a,b=common[column+'_old'],common[column+'_new'];mask=~(a.eq(b)|(a.isna()&b.isna()))
            changes.extend({'timestamp':r['interval_end_berlin'],'parameter':column,'old':r[column+'_old'],'new':r[column+'_new']} for r in common.loc[mask].to_dict('records'))
    audit['corrections_since_previous_fetch']=changes
    audit['attribution']=ATTRIBUTION
    # The independent daily product is archived before completing the snapshot.
    daily_urls=cache.links(BASE+'daily/kl/recent/')
    daily_url=next((u for u in daily_urls if re.search('_'+station+r'_akt\.zip$',u)),None)
    if daily_url is None:raise ValueError('Independent DWD daily product unavailable')
    daily_payload=cache.get(daily_url)
    daily_check=check_daily_rain(result,daily_payload,start,end)
    audit['daily_rain_check']=daily_check
    for u in cache.links(BASE+'daily/kl/'):
        if 'BESCHREIBUNG' in u and u.endswith('.pdf'):cache.get(u)
    audit['raw_manifest_files']=[r['file'] for r in cache.rows]
    (cache.directory/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    # Analysis tables derive exclusively from this normalized CSV after publication.
    candidate.replace(processed/'meteo_10min.csv')
    (processed/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    saved={'start':start,'end':end,'station':station,'fetched_at_utc':cache.fetched_at,
           'raw_directory':cache.directory.relative_to(root).as_posix(),
           'processed_sha256':sha256((processed/'meteo_10min.csv').read_bytes()),
           'audit_sha256':sha256((processed/'audit.json').read_bytes()),'attribution':ATTRIBUTION}
    state.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
    return saved


def check_daily_rain(weather,payload,start,end):
    """Daily/kl RSK since 2001: 05:50 UTC to 05:50 UTC next day (DWD docs).

    The independently downloaded description is retained alongside the source.
    Missing 10-minute bins or substituted stations are reported as incomparable.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name=next(n for n in archive.namelist() if n.startswith('produkt_klima_tag_'))
        daily=pd.read_csv(io.BytesIO(archive.read(name)),sep=';')
        metadata_names=[n for n in archive.namelist() if n.startswith('Metadaten_Parameter_') and n.endswith('.txt')]
        metadata=read_text_table(archive.read(metadata_names[0])) if metadata_names else []
    daily.columns=daily.columns.str.strip();rows=[]
    for r in daily.to_dict('records'):
        day=pd.Timestamp(str(r['MESS_DATUM'])).strftime('%Y-%m-%d')
        if not start<=day<=end:continue
        eligible=[m for m in metadata if m.get('Parameter')=='RSK' and m['Von_Datum']<=day.replace('-','')<=m['Bis_Datum']]
        if metadata and (not eligible or '05:51-05:50' not in eligible[-1].get('Zusatz-Info','')):
            raise ValueError('Daily precipitation reference period needs explicit metadata support')
        a=pd.Timestamp(day,tz='UTC')+pd.Timedelta(hours=5,minutes=50);b=a+pd.Timedelta(days=1)
        part=weather.loc[(weather.index>a)&(weather.index<=b)]
        valid=len(part)==144 and part.rain_mm.notna().all() and part.precipitation_station.nunique()==1 and int(part.precipitation_station.iloc[0])==int(r['STATIONS_ID'])
        value=float(r['RSK']);observed=float(part.rain_mm.sum()) if valid else None
        delta=observed-value if observed is not None and value!=-999 else None
        rows.append({'day':day,'begin_utc':a.isoformat(),'end_utc':b.isoformat(),'sum_10min_mm':observed,
                     'daily_rsk_mm':None if value==-999 else value,'difference_mm':delta,
                     'over_0_5_mm':bool(delta is not None and abs(delta)>.5),
                     'comparable':valid,'reason':'' if valid else 'Missing bins or substitute station'})
    if len(rows)!=len(pd.date_range(start,end)):raise ValueError('Independent daily reference has missing dates')
    return rows


def read_processed(path):
    frame=pd.read_csv(path,dtype={p+'_station':str for p in ROLES})
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop('interval_end_berlin'),utc=True)).tz_convert(TZ)
    frame['interval_start_berlin']=pd.to_datetime(frame.interval_start_berlin,utc=True).dt.tz_convert(TZ)
    return frame

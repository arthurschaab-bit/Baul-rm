"""Events and reproducible weather sensitivity from the processed DWD table only."""
from __future__ import annotations
from dataclasses import dataclass,asdict
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .report_core import energetic_laeq

TZ='Europe/Berlin'
VARIANTS={'ausweisen':('A_Ausweisung','Variante A – Wetter ausgewiesen'),
          'ausschliessen':('B_Ausschluss','Variante B – Wetterperioden ausgeschlossen')}


@dataclass(frozen=True)
class MeteoConfig:
    meteo_mode:str='ausweisen'
    rain_buffer_min:int=10
    mean_wind_ms:float=5.
    gust_ms:float=10.
    rain_light_mm_h:float=2.5
    rain_heavy_mm_h:float=10.

    def __post_init__(self):
        if self.meteo_mode not in VARIANTS:raise ValueError('Unknown meteo_mode')
        if self.rain_buffer_min<0 or self.mean_wind_ms<=0 or self.gust_ms<=0:raise ValueError('Invalid weather thresholds')
        if not 0<self.rain_light_mm_h<self.rain_heavy_mm_h:raise ValueError('Invalid intensity classes')


def rain_class(intensity,config=MeteoConfig()):
    if pd.isna(intensity):return 'unbekannt'
    return 'leicht' if intensity<config.rain_light_mm_h else 'mäßig' if intensity<=config.rain_heavy_mm_h else 'stark'


def beaufort(ms):
    if pd.isna(ms):return None
    return int(np.searchsorted([.3,1.6,3.4,5.5,8.,10.8,13.9,17.2,20.8,24.5,28.5,32.7],ms,side='right'))


def _runs(index,active,bridge_bins=0,known=None):
    """Half-open periods. At most one KNOWN dry bin can bridge rain events.

    Missing bins always split; they are neither dry nor measured wet periods.
    """
    pos=np.flatnonzero(np.asarray(active,dtype=bool));result=[]
    if not len(pos):return result
    start=previous=pos[0]
    for current in pos[1:]:
        missing=current-previous-1
        regular=(index[current]-index[previous]).total_seconds()==(current-previous)*600
        can_bridge=missing<=bridge_bins and regular and (known is None or np.asarray(known)[previous+1:current].all())
        if not can_bridge:result.append((start,previous));start=current
        previous=current
    result.append((start,previous));return result


def events(weather,config=MeteoConfig()):
    rows=[]
    rain=(weather.rain_mm>0)|(weather.rain_duration_min>0)
    wind=(weather.wind_mean_ms>=config.mean_wind_ms)|(weather.gust_ms>=config.gust_ms)
    known_rain=weather[['rain_mm','rain_duration_min']].notna().all(axis=1)
    for kind,active,bridge,known in [('Niederschlag',rain,1,known_rain),('Wind',wind,0,None)]:
        for first,last in _runs(weather.index,active,bridge,known):
            part=weather.iloc[first:last+1];begin=part.index[0]-pd.Timedelta(minutes=10);end=part.index[-1]
            mean=float(part.wind_mean_ms.mean());peak=float(part.wind_mean_ms.max());gust=float(part.gust_ms.max())
            direction=part.wind_direction_deg.dropna()
            # Circular mean, weighted by wind speed. Calm/undefined directions
            # are omitted; no arithmetic averaging across north (0/360 degrees).
            if len(direction):
                weights=part.loc[direction.index,'wind_mean_ms'].to_numpy()
                angle=np.deg2rad(direction.to_numpy());x=np.sum(np.cos(angle)*weights);y=np.sum(np.sin(angle)*weights)
                prevailing=float(np.rad2deg(np.arctan2(y,x))%360) if np.hypot(x,y)>1e-9 else None
            else:prevailing=None
            intensity=float(part.rain_mm.max()*6)
            station_fields=['precipitation_station'] if kind=='Niederschlag' else ['wind_station','extreme_wind_station']
            stations=sorted({str(x).zfill(5) for f in station_fields for x in part[f].dropna().unique()})
            rows.append({'begin':begin.isoformat(),'end_exclusive':end.isoformat(),'type':kind,
                         'duration_min':(end-begin).total_seconds()/60,
                         'rain_mm':float(part.rain_mm.sum(min_count=1)) if kind=='Niederschlag' else None,
                         'rain_minutes':float(part.rain_duration_min.sum(min_count=1)) if kind=='Niederschlag' else None,
                         'max_intensity_mm_h':intensity if kind=='Niederschlag' else None,
                         'class':rain_class(intensity,config) if kind=='Niederschlag' else f'Bft {beaufort(peak)} (Mittelwindmaximum)',
                         'wind_mean_ms':mean if kind=='Wind' else None,'wind_max_ms':peak if kind=='Wind' else None,
                         'gust_max_ms':gust if kind=='Wind' else None,'direction_deg':prevailing if kind=='Wind' else None,
                         'stations':','.join(stations)})
    return sorted(rows,key=lambda r:(r['begin'],r['type']))


def exclusion_ranges(event_rows,config=MeteoConfig()):
    rows=[]
    for event in event_rows:
        rain=event['type']=='Niederschlag'
        begin=pd.Timestamp(event['begin']);end=pd.Timestamp(event['end_exclusive'])+pd.Timedelta(minutes=config.rain_buffer_min if rain else 0)
        rows.append({'begin_second':int(begin.timestamp()),'end_second_exclusive':int(end.timestamp()),
                     'begin_berlin':begin.isoformat(),'end_exclusive_berlin':end.isoformat(),
                     'reason':'Niederschlag + Nachlauf' if rain else 'Windschwelle',
                     'rain_mm':event['rain_mm'],'max_intensity_mm_h':event['max_intensity_mm_h'],
                     'wind_max_ms':event['wind_max_ms'],'gust_max_ms':event['gust_max_ms'],
                     'buffer_min':config.rain_buffer_min if rain else 0,'stations':event['stations']})
    return rows


def interval_mask(seconds,ranges,reason=None):
    """Union on integer seconds; right endpoint is excluded, no interpolation."""
    values=np.asarray(seconds,dtype=np.int64)
    order=np.argsort(values);ordered=values[order]
    change=np.zeros(len(values)+1,dtype=np.int64)
    for r in ranges:
        if reason is None or r['reason']==reason:
            a,b=np.searchsorted(ordered,[r['begin_second'],r['end_second_exclusive']])
            change[a]+=1;change[b]-=1
    mask=np.zeros(len(values),dtype=bool);mask[order]=np.cumsum(change[:-1])>0
    return mask


def coverage_tier(count):
    return 'hohe Abdeckung' if count>=42120 else 'Teilerfassung' if count>=32760 else 'Messfenster' if count else 'keine Tagwerte'


def _metric(frame):
    level=energetic_laeq(frame.leq)
    return {'seconds':len(frame),'coverage':len(frame)/46800,'tier':coverage_tier(len(frame)),
            'laeq':level,'lower_bound':level+10*np.log10(len(frame)/46800) if len(frame) else None,
            'over55':bool(level is not None and level>55),'over60':bool(level is not None and level>60)}


def sensitivity(data,days,ranges,analysis_starts=None):
    rows=[];analysis_starts=analysis_starts or {}
    for day in days:
        a=int(pd.Timestamp(day+' 07:00',tz=TZ).timestamp());b=int(pd.Timestamp(day+' 20:00',tz=TZ).timestamp())
        part=data.loc[(data.index>=max(a,analysis_starts.get(day,a)))&(data.index<b)]
        rain=interval_mask(part.index,ranges,'Niederschlag + Nachlauf');wind=interval_mask(part.index,ranges,'Windschwelle')
        row={'day':day,'rain_affected_seconds':int(rain.sum()),'wind_affected_seconds':int(wind.sum())}
        for name,mask in [('A',np.zeros(len(part),dtype=bool)),('no_rain',rain),('no_wind',wind),('B',rain|wind)]:
            row.update({name+'_'+k:v for k,v in _metric(part.loc[~mask]).items()})
        row['difference_B_minus_A']=row['B_laeq']-row['A_laeq'] if row['B_laeq'] is not None and row['A_laeq'] is not None else None
        row['reason']=' + '.join(n for n,m in [('Niederschlag/Nachlauf',rain),('Wind',wind)] if m.any()) or 'kein Ausschluss'
        row['changed']=any(row['A_'+k]!=row[v+'_'+k] for k in ('tier','over55','over60') for v in ('no_rain','no_wind','B'))
        rows.append(row)
    return pd.DataFrame(rows)


def daily_weather(weather,day,event_rows,pce_seconds=()):
    """Totals for 00-07, 07-20, 20-24. Ten-minute bins align with boundaries."""
    midnight=pd.Timestamp(day,tz=TZ);nextday=midnight+pd.DateOffset(days=1);rows=[]
    for label,a,b in [('vor07',midnight,midnight+pd.Timedelta(hours=7)),
                      ('07-20',midnight+pd.Timedelta(hours=7),midnight+pd.Timedelta(hours=20)),
                      ('ab20',midnight+pd.Timedelta(hours=20),nextday)]:
        part=weather.loc[(weather.index>a)&(weather.index<=b)]
        expected=int((b-a).total_seconds()/600)
        complete=len(part)==expected and part[['rain_mm','rain_duration_min','wind_mean_ms','gust_ms']].notna().all().all()
        matches=[r for r in event_rows if pd.Timestamp(r['begin'])<b and pd.Timestamp(r['end_exclusive'])>a]
        rain=[r for r in matches if r['type']=='Niederschlag'];wind=[r for r in matches if r['type']=='Wind']
        values=np.asarray(pce_seconds,dtype=np.int64);values=values[(values>=int(a.timestamp()))&(values<int(b.timestamp()))]
        ranges=exclusion_ranges(matches,MeteoConfig(rain_buffer_min=0))
        rows.append({'day':day,'segment':label,'rain_mm':float(part.rain_mm.sum(min_count=expected)) if len(part) else None,
                     'rain_events':len(rain),'rain_minutes':float(part.rain_duration_min.sum(min_count=expected)) if len(part) else None,
                     'wind_minutes':sum(max(0,(min(pd.Timestamp(r['end_exclusive']),b)-max(pd.Timestamp(r['begin']),a)).total_seconds()/60) for r in wind),
                     'max_intensity_mm_h':float(part.rain_mm.max()*6) if len(part) else None,
                     'gust_max_ms':float(part.gust_ms.max()) if len(part) else None,
                     'pce_seconds':len(values),'rain_pce_fraction':float(interval_mask(values,ranges,'Niederschlag + Nachlauf').mean()) if len(values) else None,
                     'wind_pce_fraction':float(interval_mask(values,ranges,'Windschwelle').mean()) if len(values) else None,
                     'complete':bool(complete),'missing_reason':'' if complete else f'{expected-len(part)} absent bins or missing parameter values',
                     'qn_rain_wind_gust':' / '.join(','.join(str(int(v)) for v in sorted(part[p+'_qn'].dropna().unique())) or '?' for p in ('precipitation','wind','extreme_wind')),
                     'stations':','.join(sorted({str(x).zfill(5) for f in ('precipitation_station','wind_station','extreme_wind_station') for x in part[f].dropna().unique()}))})
    return rows


def prepare_variants(data,weather,days,output,config=MeteoConfig(),analysis_starts=None):
    if not data.index.is_unique or not data.index.is_monotonic_increasing:raise ValueError('PCE seconds must be unique and sorted')
    if not data['source'].eq('PCE_323').all():raise ValueError('Only selected pure PCE data are eligible')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    event_rows=events(weather,config);ranges=exclusion_ranges(event_rows,config)
    comparison=sensitivity(data,days,ranges,analysis_starts)
    a=data.copy();b=data.loc[~interval_mask(data.index,ranges)].copy()
    if not b.index.isin(a.index).all():raise ValueError('Variant B must be a subset of A')
    if not comparison.B_coverage.le(comparison.A_coverage).all():raise ValueError('B coverage exceeds A')
    comparison.to_csv(output/'Vergleich_A_B.csv',index=False,sep=';',encoding='utf-8-sig')
    pd.DataFrame(ranges).to_csv(output/'ausschluesse_meteo.csv',index=False,sep=';',encoding='utf-8-sig')
    pd.DataFrame(event_rows).to_csv(output/'Wetterereignisse.csv',index=False,sep=';',encoding='utf-8-sig')
    pd.DataFrame([r for day in days for r in daily_weather(weather,day,event_rows,a.index)]).to_csv(output/'Wetter_je_Tag_und_Nachtsegment.csv',index=False,sep=';',encoding='utf-8-sig')
    (output/'meteo_config.json').write_text(json.dumps(asdict(config),ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ausweisen':a,'ausschliessen':b},comparison,event_rows,ranges


def require_both_variants(output):
    required=[f'{kind}_{suffix}.pdf' for kind in ('Gesamtbericht','Kompaktbericht') for suffix,_ in VARIANTS.values()]
    missing=[name for name in required if not (Path(output)/name).is_file() or not (Path(output)/name).stat().st_size]
    if missing:raise ValueError('Incomplete paired report run: '+', '.join(missing))

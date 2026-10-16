"""Native weather figures and a single shared A/B comparison PDF asset."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import fitz
from .meteo_analysis import MeteoConfig,VARIANTS,rain_class,daily_weather

BLUE='#1F4E79'


def _value(x,decimals=1):
    return '—' if pd.isna(x) else f'{x:.{decimals}f}'.replace('.',',')


def _banner(title,subtitle=''):
    fig=plt.figure(figsize=(14,10.5));ax=fig.add_axes([0,0,1,1]);ax.axis('off')
    ax.add_patch(plt.Rectangle((0,.92),1,.08,color=BLUE,transform=ax.transAxes))
    fig.text(.055,.95,title,fontsize=15,color='white',weight='bold')
    fig.text(.055,.891,subtitle,fontsize=8,color='#444444')
    return fig,ax


def _table(ax,headers,rows,bbox,size=8,widths=None):
    table=ax.table(cellText=rows,colLabels=headers,cellLoc='center',bbox=bbox,colWidths=widths)
    table.auto_set_font_size(False);table.set_fontsize(size)
    for (r,c),cell in table.get_celld().items():
        cell.set_edgecolor('#D5DEE7');cell.set_linewidth(.4)
        if r==0:cell.set_facecolor(BLUE);cell.get_text().set_color('white');cell.get_text().set_weight('bold')
        else:cell.set_facecolor('#EAF1F8' if r%2 else 'white')
    return table


def event_rows_for_day(day,event_rows):
    a=pd.Timestamp(day,tz='Europe/Berlin');b=a+pd.DateOffset(days=1)
    return [r for r in event_rows if pd.Timestamp(r['begin'])<b and pd.Timestamp(r['end_exclusive'])>a]


def event_label(row,day):
    a,b=pd.Timestamp(row['begin']),pd.Timestamp(row['end_exclusive'])
    stamp=lambda x:x.strftime('%H:%M') if x.strftime('%Y-%m-%d')==day else x.strftime('%d.%m.%H:%M')
    label=stamp(a)+'–'+stamp(b)
    if row['type']=='Niederschlag':
        return f"{label} Regen {_value(row['rain_mm'],2)} mm; max. {_value(row['max_intensity_mm_h'])} mm/h; {row['class']}"
    return f"{label} Wind Ø/max. {_value(row['wind_mean_ms'])}/{_value(row['wind_max_ms'])}; Böe {_value(row['gust_max_ms'])} m/s; {_value(row['direction_deg'],0)}°; {row['class']}"


def decorate_daily(fig,day,weather,event_rows,ranges,mode,config=MeteoConfig()):
    """Retain the report's daily text block; reserve native weather panels above it."""
    noise=fig.axes[0]
    begin=pd.Timestamp(day+' 07:00');end=pd.Timestamp(day+' 20:00')
    noise.set_position([.075,.650,.865,.268]);noise.set_xlim(begin,end)
    noise.tick_params(axis='x',labelbottom=False)
    # Measured gaps keep the existing //// hatch. Weather removals get xxx.
    if mode=='ausschliessen':
        for r in ranges:
            a=pd.Timestamp(r['begin_berlin']).tz_localize(None);b=pd.Timestamp(r['end_exclusive_berlin']).tz_localize(None)
            if a<end and b>begin:
                noise.axvspan(max(a,begin),min(b,end),facecolor='#EEE0BB',hatch='xxx',edgecolor='#8A6335',alpha=.65,lw=0,zorder=1.5)
        fig.text(.075,.623,'xxx = Wetter ausgeschlossen (Regen/Nachlauf oder Wind); //// = Messlücke. Gründe und Zeiten siehe Ereignistabelle.',fontsize=6.8,color='#6B4B20')
    else:fig.text(.075,.623,'Wetterband: Stationsmessung; keine rechnerische Pegelkorrektur. Alle gültigen PCE-Sekunden bleiben enthalten.',fontsize=6.8,color='#555555')
    a=begin.tz_localize('Europe/Berlin');b=end.tz_localize('Europe/Berlin')
    part=weather.loc[(weather.index>a)&(weather.index<=b)]
    axis=fig.add_axes([.075,.500,.865,.095]);wind=axis.twinx()
    dates=(part.index-pd.Timedelta(minutes=10)).tz_localize(None)
    styles={'leicht':('#C3DCEB','..'),'mäßig':('#6C9DBD','//'),'stark':('#295E83','xx')}
    for name,(color,hatch) in styles.items():
        mask=np.asarray([rain_class(v*6,config)==name for v in part.rain_mm])
        axis.bar(dates[mask],part.rain_mm.to_numpy()[mask],width=10/1440,align='edge',color=color,edgecolor='#4D4D4D',hatch=hatch,lw=.25)
    centers=dates+pd.Timedelta(minutes=5)
    wind.plot(centers,part.wind_mean_ms,color='#202020',lw=.85,label='Mittelwind')
    wind.scatter(centers,part.gust_ms,s=5,facecolors='none',edgecolors='#202020',linewidths=.5,label='Böen')
    wind.axhline(config.mean_wind_ms,color='#777777',ls='--',lw=.7)
    wind.axhline(config.gust_ms,color='#777777',ls=':',lw=.7)
    axis.set_ylabel('mm/10min',fontsize=6.5);wind.set_ylabel('m/s',fontsize=6.5)
    axis.set_ylim(0,max(.5,float(part.rain_mm.max())*1.15));wind.set_ylim(0,max(config.gust_ms*1.15,float(part.gust_ms.max())*1.15))
    axis.set_xlim(begin,end);axis.xaxis.set_major_locator(mdates.HourLocator());axis.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    axis.tick_params(labelsize=6);wind.tick_params(labelsize=6);axis.grid(axis='y',lw=.3,color='#DDDDDD')
    qn=' / '.join(','.join(str(int(v)) for v in sorted(part[p+'_qn'].dropna().unique())) or '?' for p in ('precipitation','wind','extreme_wind'))
    fig.text(.075,.475,f'Regen .. leicht / // mäßig / xx stark; Linie = Mittel, ○ = Böe; Schwellen {config.mean_wind_ms:g}/{config.gust_ms:g} m/s. Station 03379; QN Regen/Wind/Böe: {qn}.',fontsize=6.5,color='#555555')
    matches=event_rows_for_day(day,event_rows)
    nights=[r for r in daily_weather(weather,day,event_rows) if r['segment']!='07-20']
    night_text=' | '.join(f"{r['segment']}: Regen {_value(r['rain_mm'],2)} mm / {_value(r['rain_minutes'])} min; Wind {_value(r['wind_minutes'])} min, Böe {_value(r['gust_max_ms'])} m/s" for r in nights)
    fig.text(.055,.461,'Nachtsegmente '+night_text,fontsize=6.4,color='#555555')
    fig.text(.055,.447,'WETTEREREIGNISSE 00–24 UHR (Beginn–Ende exklusiv; m/s bei Wind; Regen in mm)',fontsize=7,weight='bold',color=BLUE)
    if not matches:
        midnight=pd.Timestamp(day,tz='Europe/Berlin');nextday=midnight+pd.DateOffset(days=1)
        full=weather.loc[(weather.index>midnight)&(weather.index<=nextday)]
        complete=len(full)==int((nextday-midnight).total_seconds()/600) and full[['rain_mm','rain_duration_min','wind_mean_ms','gust_ms']].notna().all().all()
        fig.text(.055,.427,'Kein Niederschlag, kein Wind über Schwelle (Station 03379)' if complete else 'Stationsdaten unvollständig: fehlende Intervalle/Parameter; siehe Prüfprotokoll.',fontsize=7,color='#444444')
    else:
        # Two columns, up to seven rows. Full events are never silently dropped.
        visible=matches[:14];half=math.ceil(len(visible)/2)
        for i,r in enumerate(visible):
            col=i//half;row=i%half
            fig.text(.055+col*.46,.432-row*.0114,event_label(r,day),fontsize=6.15,color='#333333')
        if len(matches)>14:
            fig.text(.055,.352,'Weitere Wetterereignisse: unmittelbar folgende Fortsetzungsseite.',fontsize=6.5,color=BLUE)


def install_daily_weather(lib,weather,event_rows,ranges,mode,config):
    original_day=lib.page_day
    def page(pp,d,number):
        original_save=lib.save_page
        def save(pp,fig,label,do_check=True):
            decorate_daily(fig,d['day'],weather,event_rows,ranges,mode,config)
            original_save(pp,fig,label,do_check=False)
            remaining=event_rows_for_day(d['day'],event_rows)[14:]
            for offset in range(0,len(remaining),28):
                extra,axis=_banner('WETTEREREIGNISSE — FORTSETZUNG',d['day']+' · '+VARIANTS[mode][1])
                rows=[[event_label(row,d['day'])] for row in remaining[offset:offset+28]]
                _table(axis,['Alle weiteren Ereignisse des Messtags'],rows,[.055,.15,.89,.69],8)
                original_save(pp,extra,'Wetterereignisse '+d['day'],do_check=False)
        lib.save_page=save
        try:original_day(pp,d,number)
        finally:lib.save_page=original_save
    lib.page_day=page


def comparison_pdf(path,csv_path,indoor_days=()):
    """Render once; insert these exact pages into both complete reports."""
    frame=pd.read_csv(csv_path,sep=';');high=frame.loc[~frame.day.isin(indoor_days)]
    totals=[]
    for v in ('A','B'):
        valid=high.loc[high[v+'_coverage']>=.9]
        totals.append(f"{v}: {len(valid)} Tage ≥90 %, {int(valid[v+'_over60'].sum())} davon >60 dB(A), Max. {_value(valid[v+'_laeq'].max())} dB(A)")
    digest=hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
    with PdfPages(path) as pdf:
        chunks=[frame.iloc[index] for index in np.array_split(np.arange(len(frame)),max(1,math.ceil(len(frame)/28)))]
        for number,chunk in enumerate(chunks,1):
            fig,ax=_banner('VARIANTE A vs. B', 'A: Gesamtbericht_A_Ausweisung.pdf  |  B: Gesamtbericht_B_Ausschluss.pdf  |  LAeq jeweils aus gemessenen Sekunden')
            rows=[]
            for r in chunk.to_dict('records'):
                rows.append([r['day'],_value(r['A_laeq']),_value(r['A_coverage']*100)+'%',r['A_tier'],_value(r['B_laeq']),_value(r['B_coverage']*100)+'%',r['B_tier'],_value(r['difference_B_minus_A']),r['reason']])
            _table(ax,['Tag','A LAeq','A Abd.','A Stufe','B LAeq','B Abd.','B Stufe','B–A dB','Ausschlussgrund'],rows,[.045,.165,.91,.69],7.3,[.10,.07,.07,.13,.07,.07,.13,.07,.19])
            fig.text(.055,.119,' | '.join(totals),fontsize=8.2,color=BLUE,weight='bold')
            fig.text(.055,.087,'Summen: reine Außenmessungen, unveränderte 46.800 s als Nenner. Innenmessungen sind in der Tagestabelle enthalten.',fontsize=7.7)
            fig.text(.055,.060,'Gemeinsame Datenquelle Vergleich_A_B.csv · SHA-256 '+digest,fontsize=6.7,color='#666666')
            fig.text(.055,.026,f'Variante A – Wetter ausgewiesen  |  Variante B – Wetterperioden ausgeschlossen  |  Vergleich {number}/{len(chunks)}',fontsize=7,color='#666666')
            pdf.savefig(fig);plt.close(fig)


def weather_overview_pdf(path,daily_csv,comparison_csv,method_lines,fetch):
    daily=pd.read_csv(daily_csv,sep=';');daily=daily.loc[daily.segment=='07-20']
    comparison=pd.read_csv(comparison_csv,sep=';')
    with PdfPages(path) as pdf:
        fig,ax=_banner('METEOROLOGISCHE RAHMENBEDINGUNGEN','DWD-Beobachtungen; alle Tabellen und Wetterbänder aus processed/meteo_10min.csv')
        y=.84
        for text in method_lines:
            import textwrap
            for line in textwrap.wrap(text,145):
                fig.text(.055,y,line,fontsize=9,color='#333333');y-=.025
            y-=.022
        fig.text(.055,.055,f"Datenbasis: Deutscher Wetterdienst · CC BY 4.0 · Abruf UTC {fetch['fetched_at_utc']}",fontsize=8,color='#666666')
        pdf.savefig(fig);plt.close(fig)
        for index in np.array_split(np.arange(len(daily)),max(1,math.ceil(len(daily)/28))):
            chunk=daily.iloc[index]
            fig,ax=_banner('METEOROLOGISCHE RAHMENBEDINGUNGEN — MESSTAGE','07–20 Uhr; getrennte Nachtsegmente: Wetter_je_Tag_und_Nachtsegment.csv. Regenanteil ohne Nachlaufpuffer.')
            rows=[[r['day'],_value(r['rain_mm'],2),str(r['rain_events']),_value(r['max_intensity_mm_h']),_value(r['rain_minutes']),_value(r['wind_minutes']),_value(r['gust_max_ms']),_value(r['rain_pce_fraction']*100)+'%',_value(r['wind_pce_fraction']*100)+'%',r['qn_rain_wind_gust']] for r in chunk.to_dict('records')]
            _table(ax,['Tag','Regen mm','Ereignisse','Max. mm/h','Regen min','Wind min','Max. Böe m/s','PCE Regen','PCE Wind','QN R/W/B'],rows,[.045,.16,.91,.69],7.6)
            fig.text(.055,.08,'Datenbasis: Deutscher Wetterdienst · Station 03379 · CC BY 4.0',fontsize=8)
            pdf.savefig(fig);plt.close(fig)
        for index in np.array_split(np.arange(len(comparison)),max(1,math.ceil(len(comparison)/25))):
            chunk=comparison.iloc[index]
            fig,ax=_banner('METEOROLOGIE — SENSITIVITÄTSRECHNUNG','LAeq / Abdeckung (%); Regen umfasst Nachlauf. * = Stufe oder Aussage >55/>60 ändert sich in mindestens einer Auswahl.')
            rows=[]
            for r in chunk.to_dict('records'):
                rows.append([r['day']+(' *' if r['changed'] else ''),*[f"{_value(r[v+'_laeq'])} / {_value(r[v+'_coverage']*100)}" for v in ('A','no_rain','no_wind','B')],r['B_tier'],r['reason']])
            _table(ax,['Tag','A alle','ohne Regen','ohne Wind','B ohne beides','Stufe B','Grund'],rows,[.045,.16,.91,.69],7.9,[.12,.13,.13,.13,.13,.16,.20])
            fig.text(.055,.085,'Keine Pegelkorrektur, keine Ersatzwerte. Vollständige Aussagen >55 / >60 für alle vier Auswahlen: Vergleich_A_B.csv.',fontsize=8)
            pdf.savefig(fig);plt.close(fig)


def append_shared_comparison(document,comparison):
    with fitz.open(comparison) as source:document.insert_pdf(source)


def comparison_stream_hashes(path,count):
    with fitz.open(path) as doc:
        return [hashlib.sha256(b''.join(doc.xref_stream(x) for x in page.get_contents())).hexdigest() for page in list(doc)[-count:]]


def validate_comparison_identity(a,b,shared):
    with fitz.open(shared) as doc:count=len(doc)
    hashes=comparison_stream_hashes(shared,count)
    if comparison_stream_hashes(a,count)!=hashes or comparison_stream_hashes(b,count)!=hashes:
        raise ValueError('A/B comparison PDF page content streams differ')
    return hashes


def finalize_pair(output,comparison_asset,commit,fetch):
    """Fail closed on incomplete/mismatched variants; manifest every deliverable."""
    from .meteo_analysis import require_both_variants
    output=Path(output);require_both_variants(output)
    if not commit or len(commit)!=40:raise ValueError('A full code commit SHA is required')
    stream_hashes=validate_comparison_identity(output/'Gesamtbericht_A_Ausweisung.pdf',
        output/'Gesamtbericht_B_Ausschluss.pdf',comparison_asset)
    for suffix,label in VARIANTS.values():
        for kind in ('Gesamtbericht','Kompaktbericht'):
            with fitz.open(output/f'{kind}_{suffix}.pdf') as doc:
                for page in doc:
                    # Embedded Arial may extract spaces as NBSP. Normalize only
                    # whitespace for validation; never alter the shared PDF asset.
                    if label not in ' '.join(page.get_text().split()):raise ValueError('Missing variant label in report')
    manifest={'commit':commit,'weather_fetch':fetch,'comparison_content_stream_sha256':stream_hashes,
              'files':[{ 'file':p.relative_to(output).as_posix(),'bytes':p.stat().st_size,
                         'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                        for p in sorted(output.rglob('*')) if p.is_file() and p.name!='manifest.json']}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest

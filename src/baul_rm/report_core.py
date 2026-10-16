"""Portable calculation rules; all case details belong in a local profile."""
from __future__ import annotations
import numpy as np
import pandas as pd


def resolve_pce_seconds(exports, database_pce):
    """Keep pure PCE exports, replace mixed seconds with separate DB PCE rows.

    Frames use a unique Unix-second index. Database rows must already be grouped
    energetically from records whose device identifies the PCE meter. Mixed rows
    without such a record, microphone rows and unknown sources are excluded.
    The input frames and original source labels are never mutated.
    """
    if not exports.index.is_unique or not database_pce.index.is_unique:
        raise ValueError('Duplicate seconds must be normalized before selection')
    source=exports.get('source_export',exports['source'])
    db_source=database_pce.get('source_export',database_pce['source'])
    if not db_source.eq('PCE_323').all():
        raise ValueError('Database replacement contains a non-PCE source')
    mixed=source.eq('GEMISCHT')
    replacement=database_pce.loc[database_pce.index.isin(exports.index[mixed])].copy()
    selected=pd.concat([exports.loc[source.eq('PCE_323')].copy(),replacement]).sort_index()
    excluded=exports.loc[~exports.index.isin(selected.index)].copy()
    assert selected.index.is_unique
    return selected,excluded,{'mixed_seconds':int(mixed.sum()),'replaced_seconds':len(replacement),
        'excluded_mixed_seconds':int((mixed&~exports.index.isin(replacement.index)).sum())}


def energetic_laeq(levels):
    values=np.asarray(levels,dtype=float);values=values[np.isfinite(values)]
    return float(10*np.log10(np.mean(10**(values/10)))) if len(values) else None


def loudest_hour(frame,day_end,min_seconds=1800):
    """Highest energetic LAeq over eligible 3600-second windows, without coverage priority."""
    if len(frame)<min_seconds:return None
    first,last=int(frame.index[0]),int(frame.index[-1])
    grid=pd.Series(np.nan,index=np.arange(first,max(last+1,first+3600)))
    grid.loc[frame.index]=10**(frame.leq.to_numpy(dtype=float)/10)
    values=grid.rolling(3600,min_periods=min_seconds).mean().iloc[3599:]
    values=values.loc[values.index<day_end].dropna()
    if values.empty:return None
    end=int(values.idxmax());window=frame.loc[end-3599:end]
    return {'end_inclusive':end,'valid_seconds':len(window),'laeq':energetic_laeq(window.leq)}


def load_private_profile(path):
    """Load a user-supplied Python report profile kept outside the checkout."""
    import importlib.util
    from pathlib import Path
    path=Path(path).resolve();checkout=Path(__file__).resolve().parents[2]
    if path.is_relative_to(checkout):
        raise ValueError('Private profiles must be stored outside the repository')
    spec=importlib.util.spec_from_file_location('local_report_profile',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not callable(getattr(module,'run',None)):raise ValueError('Profile must define run(core)')
    return module


def duration_phases(series):
    """Qualify ten measured high LAeq minutes; count only >60 dB seconds.

    A wholly missing minute can join two qualifying runs only when the actual
    logging gap between their measured seconds is at most 60 seconds. Measured
    low minutes always split the run. Duration is threshold seconds / 60, so
    it cannot exceed the separately displayed >60 dB time.
    """
    values=series.dropna().sort_index()
    if values.empty:return []
    minute=values.index.floor('min')
    energy=pd.Series(10**(values.to_numpy(dtype=float)/10),index=minute)
    means=energy.groupby(level=0).mean()
    minute_groups=values.groupby(minute)
    first_by_minute=minute_groups.apply(lambda part:part.index[0])
    last_by_minute=minute_groups.apply(lambda part:part.index[-1])
    high=means[means>=10**6]
    if high.empty:return []
    groups=[];current=[high.index[0]]
    for instant in high.index[1:]:
        previous=current[-1]
        between=means.loc[(means.index>previous)&(means.index<instant)]
        gap=(instant-previous).total_seconds()
        sample_gap=(first_by_minute.loc[instant]-last_by_minute.loc[previous]).total_seconds()
        bridge=(gap==120 and between.empty and sample_gap<=61)
        if (gap==60 and sample_gap<=61) or bridge:current.append(instant)
        else:groups.append(current);current=[instant]
    groups.append(current)
    phases=[]
    for group in groups:
        if len(group)<10:continue
        selected=values[values.index.floor('min').isin(group)]
        above=selected[selected>60]
        if len(above)<600:continue
        first,last=selected.index[0],selected.index[-1]
        n=int(len(above));observed=int(len(selected))
        phases.append({'Start':first.strftime('%H:%M:%S'),'Ende':last.strftime('%H:%M:%S'),
            'Dauer_min':n/60,'Gemessene_Sekunden':observed,
            'Luecken_Sekunden':int((last-first).total_seconds())+1-observed,
            'Ueber60_Sekunden':n,'Kriterium_Minuten':len(group)})
    return phases


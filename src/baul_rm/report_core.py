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
    if not selected.index.is_unique:
        raise ValueError('Source selection produced duplicate seconds')
    delta = replacement.leq.subtract(exports.loc[replacement.index, 'leq'])
    return selected,excluded,{'mixed_seconds':int(mixed.sum()),'replaced_seconds':len(replacement),
        'excluded_mixed_seconds':int((mixed&~exports.index.isin(replacement.index)).sum()),
        'replacement_differences': [
            {'second': int(second), 'export_leq': float(exports.loc[second, 'leq']),
             'database_leq': float(replacement.loc[second, 'leq']), 'delta_db': float(value),
             'original_source': str(source.loc[second])}
            for second, value in delta.items()]}


def energetic_laeq(levels):
    values=np.asarray(levels,dtype=float);values=values[np.isfinite(values)]
    return float(10*np.log10(np.mean(10**(values/10)))) if len(values) else None


def loudest_hour(frame,day_end,min_seconds=1800,day_start=None):
    """Highest energetic LAeq over eligible 3600-second windows, without coverage priority."""
    if not frame.index.is_unique:
        raise ValueError('Duplicate seconds must be normalized before window calculation')
    frame=frame.sort_index()
    if day_start is None:
        day_start=int(frame.index.min()) if len(frame) else day_end
    frame=frame.loc[(frame.index>=day_start)&(frame.index<day_end)]
    frame=frame.loc[np.isfinite(frame.leq.to_numpy(dtype=float))]
    if len(frame)<min_seconds or day_end-day_start<3600:return None
    first,last=int(frame.index[0]),int(frame.index[-1])
    # Preserve the report's measurement-span rule: first eligible window starts
    # at the first measured second; no new trailing windows after the last
    # measured second (except the existing short-span 3600-second window).
    grid=pd.Series(np.nan,index=np.arange(first,min(day_end,max(last+1,first+3600))))
    grid.loc[frame.index]=10**(frame.leq.to_numpy(dtype=float)/10)
    values=grid.rolling(3600,min_periods=min_seconds).mean().iloc[3599:]
    values=values.loc[values.index<day_end].dropna()
    if values.empty:return None
    end=int(values.idxmax());window=frame.loc[end-3599:end]
    return {'end_inclusive':end,'valid_seconds':len(window),'laeq':energetic_laeq(window.leq)}


def require_external_path(path, checkout_root):
    """Reject resolved private input/output paths inside the explicit public checkout."""
    from pathlib import Path
    resolved=Path(path).resolve()
    if resolved.is_relative_to(Path(checkout_root).resolve()):
        raise ValueError('Private inputs and outputs must be stored outside the repository')
    return resolved


def load_private_profile(path, checkout_root):
    """Execute trusted local Python only. A profile has arbitrary Python permissions."""
    import importlib.util
    from pathlib import Path
    path=require_external_path(path, checkout_root)
    if not path.is_file():
        raise ValueError('Private Python profile does not exist')
    spec=importlib.util.spec_from_file_location('local_report_profile',path)
    if spec is None or spec.loader is None:
        raise ValueError('Private profile must be a loadable Python file')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not callable(getattr(module,'run',None)):raise ValueError('Profile must define run(core)')
    return module


def duration_phases(series, excluded_intervals=()):
    """Qualify ten measured high LAeq minutes; count only >60 dB seconds.

    A wholly missing minute can join two qualifying runs only when the actual
    logging gap between their measured seconds is at most 60 seconds. Measured
    low minutes always split the run. Duration is threshold seconds / 60, so
    it cannot exceed the separately displayed >60 dB time.
    """
    values=series.dropna().sort_index()
    if values.empty:return []
    if excluded_intervals:
        # Segment before minute aggregation: even one excluded second inside a
        # minute must split a phase. Never bridge an explicit weather exclusion.
        pieces=[];start=values.index[0]
        for a,b in sorted((pd.Timestamp(a),pd.Timestamp(b)) for a,b in excluded_intervals):
            if b<=start:continue
            if a>values.index[-1]:break
            pieces.extend(duration_phases(values.loc[(values.index>=start)&(values.index<a)]))
            start=max(start,b)
        pieces.extend(duration_phases(values.loc[values.index>=start]))
        return pieces
    minute=values.index.floor('min')
    energy=pd.Series(10**(values.to_numpy(dtype=float)/10),index=minute)
    means=energy.groupby(level=0).mean()
    bounds=pd.DataFrame({'first':values.index,'last':values.index},index=minute).groupby(level=0).agg({'first':'min','last':'max'})
    first_by_minute=bounds['first'];last_by_minute=bounds['last']
    high=means[means>=10**6*(1-1e-12)]
    if high.empty:return []
    groups=[];current=[high.index[0]]
    for instant in high.index[1:]:
        previous=current[-1]
        between_count=means.index.searchsorted(instant)-means.index.searchsorted(previous,side='right')
        gap=(instant-previous).total_seconds()
        sample_gap=(first_by_minute.loc[instant]-last_by_minute.loc[previous]).total_seconds()
        bridge=(gap==120 and between_count==0 and sample_gap<=61)
        barrier=any(pd.Timestamp(a)<=first_by_minute.loc[instant] and pd.Timestamp(b)>last_by_minute.loc[previous] for a,b in excluded_intervals)
        if not barrier and ((gap==60 and sample_gap<=61) or bridge):current.append(instant)
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


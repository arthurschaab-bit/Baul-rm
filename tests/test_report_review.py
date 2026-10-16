import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from baul_rm.report_core import (duration_phases, energetic_laeq, loudest_hour,
                                load_private_profile, require_external_path, resolve_pce_seconds)
from baul_rm.report_inputs import read_database_day


def test_hour_exact_energy_count_bounds_and_order():
    frame=pd.DataFrame({'leq':np.r_[np.repeat(80.,1800),np.repeat(40.,5400)]},index=np.arange(7200))
    frame=frame.drop(index=np.arange(1800,2600)).iloc[::-1]
    result=loudest_hour(frame,7200,day_start=0)
    assert result['end_inclusive']==3599
    assert result['valid_seconds']==2800
    assert result['laeq']==pytest.approx(10*np.log10((1800*1e8+1000*1e4)/2800))
    assert loudest_hour(frame,1799,day_start=0) is None
    assert loudest_hour(frame,7200,day_start=6000) is None
    assert loudest_hour(frame.iloc[:1799],7200,day_start=0) is None


def test_phase_boundaries_quiet_minutes_and_weather_barriers():
    index=pd.date_range('2001-01-02 07:00',periods=1200,freq='s',tz='Europe/Berlin')
    data=pd.Series(61.,index=index)
    assert len(duration_phases(data.iloc[:600]))==1
    assert duration_phases(data.iloc[:599])==[]
    assert duration_phases(pd.Series(60.,index=index))==[]
    # Only the last second before and first after a wholly missing minute remain.
    missing=data.drop(index=index[300:360])
    assert len(duration_phases(missing))==1
    quiet=data.copy();quiet.iloc[300:360]=40.
    assert len(duration_phases(quiet))==1
    assert duration_phases(quiet)[0]['Start']=='07:06:00'
    assert duration_phases(data.iloc[:600],[(index[299],index[300])])==[]


def test_empty_energy_and_original_source_label():
    assert energetic_laeq([np.nan,np.inf]) is None
    export=pd.DataFrame({'leq':[99.],'source':['PCE_323'],'source_export':['GEMISCHT']},index=[1])
    db=pd.DataFrame({'leq':[61.],'source':['PCE_323'],'source_export':['PCE_323']},index=[1])
    selected,_,audit=resolve_pce_seconds(export,db)
    assert selected.loc[1,'leq']==61
    assert audit['replacement_differences'][0]['delta_db']==-38
    assert export.loc[1,'source_export']=='GEMISCHT'


def test_private_path_and_profile_validation(tmp_path):
    checkout=tmp_path/'repo';checkout.mkdir()
    with pytest.raises(ValueError):require_external_path(checkout/'..'/'repo'/'audit.json',checkout)
    invalid=tmp_path/'bad.txt';invalid.write_text('not python')
    with pytest.raises(ValueError,match='loadable'):load_private_profile(invalid,checkout)
    profile=tmp_path/'profile.py';profile.write_text('def run(core): return 42')
    assert load_private_profile(profile,checkout).run(None)==42


def test_database_null_flags_and_levels_are_excluded():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript('CREATE TABLE sessions(id INTEGER,deviceName TEXT); CREATE TABLE measurements(id INTEGER,sessionId INTEGER,timestamp INTEGER,levelDb REAL,weighting TEXT,flags INTEGER,timeWeighting TEXT); INSERT INTO sessions VALUES(1,"PCE-323");')
        second=int(pd.Timestamp('2001-01-02 07:00',tz='Europe/Berlin').timestamp())
        connection.executemany('INSERT INTO measurements VALUES(?,?,?,?,?,?,?)',[(1,1,second*1000,60,'A',None,'FAST'),(2,1,(second+1)*1000,None,'A',0,'FAST'),(3,1,(second+2)*1000,62,'A',0,'FAST')])
        audit={'database_days':[]}
        result=read_database_day(connection,'2001-01-02',audit,'synthetic')
        assert result.index.tolist()==[second+2]
        assert audit['database_days'][0]['excluded']==2

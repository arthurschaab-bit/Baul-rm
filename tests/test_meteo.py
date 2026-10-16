import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from baul_rm.meteo_dwd import schema_from_metadata,normalize_archive,read_processed,check_daily_rain
from baul_rm.meteo_analysis import (MeteoConfig,events,rain_class,exclusion_ranges,
    interval_mask,prepare_variants,require_both_variants,daily_weather)


def weather():
    index=pd.date_range('2026-10-25 00:00',periods=170,freq='10min',tz='UTC').tz_convert('Europe/Berlin')
    result=pd.DataFrame({'rain_mm':0.,'rain_duration_min':0.,'rain_indicator':0.,
                        'wind_mean_ms':1.,'wind_direction_deg':350.,'gust_ms':2.},index=index)
    for p in ('precipitation','wind','extreme_wind'):
        result[p+'_station']='03379';result[p+'_qn']=3
    return result


def zipped(name,text):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:archive.writestr(name,text)
    return stream.getvalue()


def test_dynamic_schema_end_intervals_missing_and_dst(tmp_path):
    meta='Stations_ID;Parameter;Parameterbeschreibung;Einheit\n3379;CUSTOM;Windgeschwindigkeitsmittel der voran. 10min;m/sec\n3379;DIRECTION;Windrichtungsmittel der voran. 10min;Grad\n'
    schema=schema_from_metadata(zipped('Metadaten_Parameter_03379.txt',meta),'wind')
    text='STATIONS_ID;MESS_DATUM;QN;CUSTOM;DIRECTION\n3379;202610250050;3;5;350\n3379;202610250100;1;-999;-999\n'
    normalized=normalize_archive(zipped('produkt_test.txt',text),schema,'wind','03379','now')
    assert normalized.index[0].isoformat()=='2026-10-25T02:50:00+02:00'
    assert normalized.index[1].isoformat()=='2026-10-25T02:00:00+01:00'
    assert pd.isna(normalized.iloc[1].wind_mean_ms)
    assert normalized.iloc[1].wind_qn==1
    assert 'nicht qualitätsgeprüft' in normalized.iloc[0].wind_status
    frame=weather();frame.index.name='interval_end_berlin';frame['interval_start_berlin']=frame.index-pd.Timedelta(minutes=10)
    path=tmp_path/'weather.csv';frame.to_csv(path)
    loaded=read_processed(path)
    assert loaded.index.is_unique
    assert (loaded.index[1]-loaded.index[0]).total_seconds()==600


def test_rain_grouping_known_dry_bridge_and_missing_split():
    frame=weather();frame.iloc[[2,4],frame.columns.get_loc('rain_mm')]=[.1,2.]
    result=events(frame)
    assert len(result)==1
    assert result[0]['duration_min']==30
    assert result[0]['rain_mm']==pytest.approx(2.1)
    assert result[0]['max_intensity_mm_h']==12
    assert result[0]['class']=='stark'
    frame.iloc[3,frame.columns.get_loc('rain_mm')]=np.nan
    assert len(events(frame))==2
    assert [rain_class(x) for x in (2.49,2.5,10,10.01,np.nan)]==['leicht','mäßig','mäßig','stark','unbekannt']


def test_wind_gust_direction_and_half_open_buffer():
    frame=weather();frame.iloc[2:4,frame.columns.get_loc('gust_ms')]=10
    frame.iloc[3,frame.columns.get_loc('wind_direction_deg')]=10
    event=events(frame)[0]
    assert event['type']=='Wind'
    assert min(event['direction_deg'],360-event['direction_deg'])<1e-8
    ranges=exclusion_ranges([event]);a=ranges[0]['begin_second'];b=ranges[0]['end_second_exclusive']
    assert interval_mask([a-1,a,b-1,b],ranges).tolist()==[False,True,True,False]
    frame.iloc[8,frame.columns.get_loc('rain_duration_min')]=1
    rain=next(r for r in exclusion_ranges(events(frame)) if r['reason'].startswith('Niederschlag'))
    assert rain['end_second_exclusive']-rain['begin_second']==1200


def test_variants_denominator_bounds_threshold_changes_and_completeness(tmp_path):
    frame=weather();day='2026-10-25'
    frame.loc[(frame.index.hour==10),'rain_mm']=1
    begin=int(pd.Timestamp(day+' 07:00',tz='Europe/Berlin').timestamp())
    data=pd.DataFrame({'leq':61.,'source':'PCE_323'},index=np.arange(begin,begin+46800))
    variants,comparison,_,_=prepare_variants(data,frame,[day],tmp_path)
    assert variants['ausschliessen'].index.isin(variants['ausweisen'].index).all()
    assert comparison.loc[0,'A_coverage']==1
    assert comparison.loc[0,'B_coverage']<1
    assert comparison.loc[0,'B_lower_bound']==pytest.approx(61+10*np.log10(comparison.loc[0,'B_seconds']/46800))
    assert (tmp_path/'Vergleich_A_B.csv').exists()
    with pytest.raises(ValueError,match='Incomplete paired'):require_both_variants(tmp_path)


def test_daily_comparison_independent_time_window():
    index=pd.date_range('2026-10-25 06:00',periods=144,freq='10min',tz='UTC')
    frame=pd.DataFrame({'rain_mm':.1,'precipitation_station':'03379'},index=index)
    payload=zipped('produkt_klima_tag_test.txt','STATIONS_ID;MESS_DATUM;RSK\n3379;20261025;14.4\n')
    rows=check_daily_rain(frame,payload,'2026-10-25','2026-10-25')
    assert rows[0]['comparable']
    assert rows[0]['difference_mm']==pytest.approx(0)


def test_invalid_configuration():
    with pytest.raises(ValueError):MeteoConfig(meteo_mode='one_variant_only')
    with pytest.raises(ValueError):MeteoConfig(rain_buffer_min=-1)

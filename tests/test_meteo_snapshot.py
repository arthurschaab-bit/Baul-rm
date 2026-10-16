import hashlib
import json
from pathlib import Path
import pandas as pd


def test_committed_dwd_snapshot_hashes_and_daily_crosscheck():
    root=Path(__file__).resolve().parents[1]/'data/meteo'
    manifest=pd.read_csv(root/'manifest.csv')
    assert len(manifest)>0
    for row in manifest.to_dict('records'):
        payload=(root/row['file']).read_bytes()
        assert len(payload)==row['bytes']
        assert hashlib.sha256(payload).hexdigest()==row['sha256'],row['file']
        assert row['url'].startswith('https://opendata.dwd.de/')
    state=json.loads((root/'processed/fetch.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((root/'processed/meteo_10min.csv').read_bytes()).hexdigest()==state['processed_sha256']
    assert hashlib.sha256((root/'processed/audit.json').read_bytes()).hexdigest()==state['audit_sha256']
    audit=json.loads((root/'processed/audit.json').read_text(encoding='utf-8'))
    assert not any(audit['missing'].values())
    assert len(audit['daily_rain_check'])==144
    assert all(r['comparable'] for r in audit['daily_rain_check'])
    assert not any(r['over_0_5_mm'] for r in audit['daily_rain_check'])

import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from baul_rm.cli import _private_report_environment
from baul_rm.meteo_analysis import require_both_variants,VARIANTS


def test_private_profile_forwarding_and_checkout_guard(tmp_path):
    checkout=tmp_path/'public';checkout.mkdir()
    env=_private_report_environment({'private_profile':'profile.py','meteo_mode':'ausschliessen'},tmp_path,checkout)
    assert env['BAUL_RM_PRIVATE_REPORT_PROFILE']==str(tmp_path/'profile.py')
    assert env['BAUL_RM_METEO_MODE']=='ausschliessen'
    with pytest.raises(ValueError):_private_report_environment({'private_profile':'public/profile.py'},tmp_path,checkout)
    with pytest.raises(ValueError):_private_report_environment({'meteo_mode':'one'},tmp_path,checkout)


def test_legacy_weather_entry_uses_explicit_dates():
    root=Path(__file__).resolve().parents[1]
    script=root/'pipeline/Verknuepfung/scripts/meteo_dwd.py'
    result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--start' in result.stdout and '--end' in result.stdout


def test_automatic_report_build_rejects_single_variant_route(tmp_path):
    root=Path(__file__).resolve().parents[1];scripts=tmp_path/'Verknuepfung/scripts';scripts.mkdir(parents=True)
    for name in ('auto_pipeline_v8.py','auto_pipeline_v10.py'):
        shutil.copy2(root/'pipeline/Verknuepfung/scripts'/name,scripts/name)
    env=dict(os.environ);env.pop('BAUL_RM_PRIVATE_REPORT_PROFILE',None);env.pop('BAUL_RM_PRIVATE_REPORT_LAUNCHER',None)
    result=subprocess.run([sys.executable,str(scripts/'auto_pipeline_v10.py'),'--reports-only'],cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode!=0
    assert 'report.private_profile' in result.stderr
    assert not list(tmp_path.rglob('*.pdf'))


def test_old_counterpart_files_cannot_pass_as_a_complete_new_run(tmp_path):
    for suffix,_ in VARIANTS.values():
        for kind in ('Gesamtbericht','Kompaktbericht'):
            path=tmp_path/f'{kind}_{suffix}.pdf';path.write_bytes(b'old output')
            os.utime(path,ns=(1_000_000_000,1_000_000_000))
    require_both_variants(tmp_path)
    with pytest.raises(ValueError,match='not all generated'):
        require_both_variants(tmp_path,created_after_ns=2_000_000_000)

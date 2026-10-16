from pathlib import Path
import fitz
import pandas as pd
import pytest
from baul_rm.meteo_report import comparison_pdf,validate_comparison_identity


def test_shared_comparison_content_is_byte_identical_in_both_reports(tmp_path):
    row={'day':'2001-01-02','reason':'Wind','difference_B_minus_A':-1}
    for v,level,coverage in [('A',65.,1.),('B',64.,.8)]:
        row.update({v+'_laeq':level,v+'_coverage':coverage,v+'_tier':'hohe Abdeckung' if coverage>=.9 else 'Teilerfassung',v+'_over60':True})
    csv=tmp_path/'Vergleich_A_B.csv';pd.DataFrame([row]).to_csv(csv,sep=';',index=False)
    shared=tmp_path/'comparison.pdf';comparison_pdf(shared,csv)
    for name in ('A','B'):
        doc=fitz.open();page=doc.new_page();page.insert_text((40,40),'Different variant '+name)
        with fitz.open(shared) as source:doc.insert_pdf(source)
        doc.save(tmp_path/f'{name}.pdf');doc.close()
    hashes=validate_comparison_identity(tmp_path/'A.pdf',tmp_path/'B.pdf',shared)
    assert len(hashes)==1
    doc=fitz.open(tmp_path/'B.pdf');doc[-1].insert_text((30,30),'changed')
    doc.save(tmp_path/'changed.pdf');doc.close()
    with pytest.raises(ValueError,match='streams differ'):
        validate_comparison_identity(tmp_path/'A.pdf',tmp_path/'changed.pdf',shared)

import unittest
import numpy as np
import pandas as pd
from baul_rm.report_core import resolve_pce_seconds,energetic_laeq,loudest_hour,duration_phases

class ReportCoreTests(unittest.TestCase):
    def test_mixed_uses_only_separate_pce_db_values(self):
        exports=pd.DataFrame({'leq':[70.,95.,98.,40.,50.],
            'source':['PCE_323','GEMISCHT','GEMISCHT','MIKROFON','UNBEKANNT']},index=[1,2,3,4,5])
        db=pd.DataFrame({'leq':[60.],'source':['PCE_323']},index=[2])
        original=exports.copy(deep=True)
        selected,excluded,audit=resolve_pce_seconds(exports,db)
        self.assertEqual(selected.index.tolist(),[1,2]);self.assertEqual(selected.loc[2,'leq'],60)
        self.assertEqual(excluded.index.tolist(),[3,4,5]);self.assertEqual(audit['excluded_mixed_seconds'],1)
        pd.testing.assert_frame_equal(original,exports)

    def test_mislabeled_database_is_rejected(self):
        frame=pd.DataFrame({'source':['GEMISCHT'],'leq':[90.]},index=[1])
        with self.assertRaises(ValueError):resolve_pce_seconds(frame,frame)

    def test_energy_mean_is_not_decibel_mean(self):
        self.assertAlmostEqual(energetic_laeq([50,70]),67.0329137812)

    def test_louder_gappy_hour_wins(self):
        frame=pd.DataFrame({'leq':np.r_[np.repeat(80.,1800),np.repeat(40.,5400)]},index=np.arange(7200))
        frame=frame.drop(index=np.arange(1800,2600))
        result=loudest_hour(frame,7200)
        self.assertGreater(result['laeq'],70);self.assertGreaterEqual(result['valid_seconds'],1800)

    def test_phase_requires_ten_minutes_of_displayed_threshold_duration(self):
        values=pd.Series(61.,index=pd.date_range('2001-01-02 07:00',periods=600,freq='s'))
        values.iloc[::2]=59.
        self.assertEqual(duration_phases(values),[])
        longer=pd.concat([values,values.set_axis(values.index+pd.Timedelta(minutes=10))])
        phases=duration_phases(longer)
        self.assertEqual(len(phases),1);self.assertEqual(phases[0]['Dauer_min'],10)

if __name__=='__main__':unittest.main()

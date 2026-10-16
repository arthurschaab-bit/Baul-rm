import tempfile,unittest,sqlite3
from pathlib import Path
from baul_rm.report_inputs import collect

class InputTests(unittest.TestCase):
    def test_pce_db_overrides_mixed_csv_but_pure_csv_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);day=root/'2001-01-02';day.mkdir()
            (day/'laermprotokoll_example.csv').write_text('Zeit;LAeq_dB;LAFmax_dB;Quelle;Samples\n2001-01-02T07:00:00+01:00;99;99;GEMISCHT;2\n2001-01-02T07:00:01+01:00;70;70;PCE_323;1\n2001-01-02T07:00:02+01:00;98;98;GEMISCHT;2\n',encoding='utf-8')
            db=root/'test.sqlite';connection=sqlite3.connect(db)
            connection.executescript('CREATE TABLE sessions(id INTEGER,deviceName TEXT);CREATE TABLE measurements(id INTEGER,sessionId INTEGER,timestamp INTEGER,levelDb REAL,weighting TEXT,flags INTEGER,timeWeighting TEXT);INSERT INTO sessions VALUES(1,"PCE-323");')
            import datetime
            second=int(datetime.datetime.fromisoformat('2001-01-02T07:00:00+01:00').timestamp())
            connection.executemany('INSERT INTO measurements VALUES(?,?,?,?,?,?,?)',[(1,1,second*1000,60,'A',0,'FAST'),(2,1,(second+1)*1000,65,'A',0,'FAST')]);connection.commit();connection.close()
            data,audit,_=collect(root,'2001-01-02','2001-01-02',[db])
            self.assertEqual(data.index.tolist(),[second,second+1])
            self.assertEqual(data.loc[second,'leq'],60);self.assertEqual(data.loc[second+1,'leq'],70)
            self.assertTrue(data.loc[second,'origin'].startswith('DB:'))

if __name__=='__main__':unittest.main()

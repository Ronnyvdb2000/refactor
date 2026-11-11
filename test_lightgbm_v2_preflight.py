import unittest
import pandas as pd
from lightgbm_v2_preflight import diagnose, FEATURES

class PreflightTests(unittest.TestCase):
    def data(self, days):
        dates = pd.bdate_range('2026-01-01', periods=days)
        rows = []
        for i, date in enumerate(dates):
            for j in range(12):
                r = {'ticker': f'T{j}', 'datum': date}
                r.update({f: float(j + 1) for f in FEATURES})
                for h in ('10d','30d','60d'):
                    r['fwd_ret_'+h] = float((j+i)%5-2)
                rows.append(r)
        return pd.DataFrame(rows)

    def test_30_dates_30d_waits(self):
        r = diagnose(self.data(30), '30d')
        self.assertEqual(r['status'], 'WAIT_DATA')
        self.assertEqual(r['train_rows'], 0)

    def test_120_dates_30d_ready(self):
        r = diagnose(self.data(120), '30d')
        self.assertEqual(r['status'], 'READY')
        self.assertGreater(r['train_rows'], 100)

    def test_no_labels_waits(self):
        df = self.data(40)
        df['fwd_ret_60d'] = None
        self.assertEqual(diagnose(df, '60d')['status'], 'WAIT_DATA')

if __name__ == '__main__':
    unittest.main()

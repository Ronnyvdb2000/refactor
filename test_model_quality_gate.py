import unittest
from model_quality_gate import evaluate_candidate

class TestQualityGate(unittest.TestCase):
    def test_weak_model_held(self):
        m = dict(auc=.493, top_model=-1.755, top_baseline=0, n_test=100,
                 n_test_dates=10, auc_relative=.49)
        self.assertEqual(evaluate_candidate(m)['publish_advice'], 'HOLD')

    def test_good_model_eligible(self):
        m = dict(auc=.653, top_model=3.285, top_baseline=.984, n_test=200,
                 n_test_dates=12, auc_relative=.61)
        self.assertEqual(evaluate_candidate(m)['publish_advice'], 'ELIGIBLE_FOR_REVIEW')

    def test_no_comparable_incumbent(self):
        m = dict(auc=.653, top_model=3.285, top_baseline=.984, n_test=200,
                 n_test_dates=12, auc_relative=.61)
        self.assertEqual(evaluate_candidate(m, {'auc':.60})['publish_advice'], 'HOLD')

if __name__ == '__main__':
    unittest.main()

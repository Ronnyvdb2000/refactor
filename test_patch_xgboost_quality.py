import unittest
from patch_xgboost_quality import patch, OLD

class PatchTests(unittest.TestCase):
    def test_patch_and_compile(self):
        src = ('def train_voor_horizon():\n' + OLD + '\n        raise\n' +
               '    return {\n        "horizon": horizon,\n        "status": "getraind",\n        "opmerking": None,\n    }\n')
        result = patch(src)
        self.assertIn('if publiceer:', result)
        self.assertIn('"afgekeurd"', result)
        compile(result, '<test>', 'exec')

    def test_reject_duplicate(self):
        with self.assertRaises(ValueError):
            patch('kwaliteitsadvies = evaluate_candidate({')

if __name__ == '__main__':
    unittest.main()

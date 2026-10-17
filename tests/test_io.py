"""Unit tests voor IO en ticker-loaders."""
from common.io import load_tickers_from_file, bouw_bestandslijst, label_voor

def test_bouw_bestandslijst_default():
    lijst = bouw_bestandslijst()
    assert lijst[0] == "tickers_041x.txt"
    assert lijst[-1] == "tickers_059x.txt"
    assert len(lijst) == 19

def test_bouw_bestandslijst_andere_suffix():
    lijst = bouw_bestandslijst(suffix="a")
    assert lijst[0] == "tickers_041a.txt"

def test_label_voor():
    assert label_voor("tickers_048x.txt") == "048 Nasdaq/NYSE"

def test_load_tickers_parseert_kommas_en_puntkommas(tmp_path):
    pad = tmp_path / "test.txt"
    pad.write_text("AAPL, MSFT;NVDA\nGOOG\n# comment\nAMZN")
    resultaat = load_tickers_from_file(str(pad))
    assert resultaat == ["AAPL", "AMZN", "GOOG", "MSFT", "NVDA"]

def test_load_tickers_ontbrekend_bestand():
    assert load_tickers_from_file("/niet/bestaand/pad.txt") == []

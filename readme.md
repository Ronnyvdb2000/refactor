Technische Handleiding: refactor repository (branch: refactor/phase-1-common)
1. Doel van de repository
Deze repository is een gerefactorde versie van de Fantasie-repo. Het doel is om de dubbele code (in 20+ bots) te centraliseren in één common/-package, de statistiek te verbeteren (Newey-West), en de onderhoudbaarheid te vergroten. De data-generatie, feature-stores en modellen draaien nu op deze centrale architectuur.

2. Mappenstructuur (gebaseerd op de door jou aangeleverde bestanden)
common/: De centrale package. Bevat alle gedeelde logica.
__init__.py: Exporteert de functies.
indicators.py: Canonieke technische indicatoren (Wilder/TA-Lib-conform).
io.py: download_history, ticker-loaders, bestandslijsten.
notify.py: send_telegram, send_email.
sizing.py: bereken_positie, trade_cost, sizing_tekst.
text.py: safe_float, today_str, esc.
tests/: Unit tests voor de common/-package.
test_indicators.py
test_io.py
test_sizing.py
examples/ (indien aanwezig): Voorbeeldscripts.
Root: De bots (bot_*.py), combineerders (a_*.py), feature-stores (bouw_*.py), analyses (analyse_*.py) en de modellen (a_par_model.py, a_meta_model*.py).
.github/workflows/: GitHub Actions workflows (momenteel uitgeschakeld via "Disable actions").

3. De common/-package (de kern)

3.1 common/indicators.py
Bevat de canonieke implementatie van alle technische indicatoren. Alle bots en feature-stores gebruiken deze functies.
wilder_smooth(series, period): De basis voor RSI, ATR, ADX.
compute_rsi_series(close, period=14)
compute_atr_series(high, low, close, period=14)
compute_adx_series(high, low, close, period=14)
compute_macd_series(close, fast=12, slow=26, signal=9)
compute_bb(close, period=20, std_mult=2.0)
compute_stoch(high, low, close, k_period=14, k_smooth=3, d_smooth=3)
compute_ibs(high, low, close)
compute_hv(close, period) (historische volatiliteit)
compute_ema(close, span)
bereken_alle_indicatoren(hist, index_ret=None): Berekent de volledige featureset voor de feature-store.

3.2 common/io.py
bouw_bestandslijst(suffix="x", start=41, eind=59): Genereert de lijst met tickerbestanden.
load_tickers_from_file(path): Leest tickers uit een bestand (komma/enter-gescheiden).
alle_tickers(suffix="x"): Haalt alle tickers op uit alle bestanden.
download_history(tickers, period="2y", start=None, end=None, auto_adjust=True): Centrale downloadfunctie met fallback naar per-ticker downloads.

3.3 common/notify.py
send_telegram(text, parse_mode="Markdown", silent_on_missing=True): Verstuurt berichten naar Telegram.
send_email(subject, body, html_body=False, silent_on_missing=True): Verstuurt e-mails via Gmail SMTP.

3.4 common/sizing.py
trade_cost(amount): Berekent de transactiekosten (vast + percentage).
bereken_positie(portfolio_waarde, entry, stop, risico_pct=0.05): Berekent het aantal aandelen op basis van risico.
bereken_atr_positie(portfolio_waarde, entry, atr, sl_mult=2.0, risico_pct=0.05): Idem, maar met ATR als stopafstand.
sizing_tekst(entry, stop, tp, portfolio_waarde, risico_pct=0.05): Genereert een geformatteerde tekst voor rapporten.

3.5 common/text.py
today_str(): Geeft de huidige datum als YYYY-MM-DD.
safe_float(val, default=nan): NaN-veilige conversie.
esc(s): HTML-escape voor Telegram.
yahoo_link(ticker): Geeft een Yahoo Finance-link.
fmt_koers(koers): Formatteert een koers.

4. De Bots (bot_*.py)

Alle bots zijn gerefactord om from common import ... te gebruiken. Ze hebben hun eigen unieke logica (criteria, scoring) maar delen de basis.
Data-ophaling: download_history uit common/io.py.
Indicatoren: compute_* uit common/indicators.py.
Notificaties: send_telegram / send_email uit common/notify.py.
Sizing: bereken_positie / trade_cost uit common/sizing.py.
Belangrijke bots:
bot_00kr.py: Kritische Selectie (maandelijkse RSI/MACD).
bot_00db.py: Darvas Box (met backtest).
bot_00vcp.py: VCP (met backtest).
bot_00cs.py: CAN SLIM (met backtest).
bot_00dm.py: Dual Momentum (met backtest).
bot_00ms.py: Minervini SEPA (met backtest).
bot_00mr.py: Multi-Systeem (IBS+RSI en ORB).
bot_00graham.py: Graham Defensive Investor.
bot_00greenblatt.py: Magic Formula (globale ranking).
bot_00oshaughnessy.py: Trending Value (VC2 + momentum).
bot_01kasstr.py: Kasstroom Onderwaardering (FCF).
bot_01hoogl.py: GARP Onderwaardering.
bot_00Fisher.py: Price-to-Sales.
bot_01beurssign.py: Beurssignalen-methodiek.
bot_01cointegr.py: Pairs Trading (co-integratie).
bot_01marktsent.py: Marktsentiment (VADER).
bot_01repititief.py: Seizoenseffecten (BH-correctie).
bot_01volhunter.py: Volatiliteits-dip scanner.
bot_00supervar.py: Superbot (Trend + VCP + Volatiliteit).
bot_00xxxV2.py: Global Engine (Traag/Snel/Hyper).
bot_01xgboost.py: XGBoost voorspelling (aandelen).
bot_01xgboostMeta.py: XGBoost voorspelling (forex).

5. Combineerders (a_*.py)

a_trade.py: Telt overlap over de laatste dagen, verrijkt met XGBoost-scores.
a_trade_q.py: Telt overlap enkel op de meest recente datum ("vers signaal").
a_trade_combi.py: Gewogen kwaliteitsscore op basis van Sharpe-ratio's van strategieën, met volatiliteitsfilter.
a_par_model.py: Lineair multi-factor model met rolling IC-gewichten (Newey-West HAC).
a_meta_model_train.py: Traint de Ridge-regressie en slaat het model op in Supabase.
a_meta_model.py: Walk-forward evaluatie van het Ridge-model.
a_meta_model_score.py: Scoort de laatste selecties met het getrainde model.
a_top_dist_sma50.py: Ad hoc script voor tickers met laagste pct_from_ma50.

6. Feature-stores (bouw_*.py)

bouw_generieke_technicals.py: Vult de generieke_technicals-tabel met indicatoren (via common/indicators.py).
bouw_generieke_fundamentals.py: Vult de generieke_fundamentals-tabel met fundamentele data.
bouw_forward_returns.py: Vult de forward_returns-tabel met de gelabelde forward returns (met split- en eenheidscorrectie).

7. Analyses (analyse_*.py en analyseer_*.py)

analyse_parameter_correlatie.py: Berekent cross-sectionele IC's en toetst deze met Newey-West HAC (voor overlappende returns) en BH-FDR-correctie.
analyse_parameter_ic.py: Berekent de IC-matrix per parameter en horizon.
analyse_forward_returns.py: Analyseert de prestaties per strategie over meerdere horizonnen.
analyse_selecties.py: Berekent rendementen per strategie (via yfinance).
analyseer_forward_correlaties.py: Test parameters tegen een langere horizon (weken).
analyseer_weekly_correlaties.py: Test parameters tegen wekelijkse prestaties.

8. Workflows (GitHub Actions)

Momenteel uitgeschakeld via "Disable actions" in de repo-instellingen (om te voorkomen dat ze draaien zonder de juiste secrets).
De workflows zijn te vinden in .github/workflows/ (o.a. run_00cs.yml, etc.).
Secrets: Er zijn nog geen secrets aangemaakt in de refactor-repo (bevestigd via screenshot). De workflows zullen falen totdat SUPABASE_DB_URL, TELEGRAM_TOKEN, etc. zijn toegevoegd.

9. Tests

Uitvoeren met: python -m pytest tests/ -v
De tests controleren de common/-package (indicatoren, sizing, io).
Verwachte output: 20 tests, allemaal geslaagd (bevestigd door jouw terminal-output).

10. Bekende problemen / Aandachtspunten

Geen nieuwe database: De refactor-repo gebruikt de bestaande Supabase-database van Fantasie. Zorg dat de Fantasie-repo (of de bijbehorende Actions) uit staat om dubbele rijen te voorkomen.
Secrets: Nog niet ingesteld in refactor. De workflows kunnen pas draaien als deze zijn toegevoegd.
Onzinbestanden: Er zijn per ongeluk 9 onzinbestanden gecommit (zoals (vmai.iloc[-1]). Deze moeten worden verwijderd met git rm (zoals eerder aangegeven).

11. Hoe te draaien

Lokaal testen: python -c "import bot_00kr; print('Import OK')" (vereist de juiste packages, zoals pandas, yfinance, psycopg2, etc.).
Live draaien: python bot_00kr.py live (vereist SUPABASE_DB_URL en andere secrets in de omgeving).
Tests: python -m pytest tests/ -v.

12. Toekomstige stappen (indien gewenst)

Verwijder de onzinbestanden.
Stel de secrets in.
Zet de Actions weer aan (gefaseerd).
Archiveer de Fantasie-repo om dubbele data te voorkomen.
Voeg de xgboost-botten toe aan de common/-structuur (indien nog niet gedaan).
Voeg CI/CD toe (pytest in GitHub Actions).

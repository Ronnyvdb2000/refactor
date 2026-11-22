# Dagelijkse onderzoekstabel sel_par

`public.sel_par` in project `fantasie` bevat één rij per aandeel (`ticker`) en
selectiedatum (`datum`). `strategieen` bewaart alle selecterende strategieën.
De dagelijkse verversing start om 04:45 UTC: 05:45 Belgische wintertijd,
06:45 zomertijd. De selectiegrens is de vorige UTC-dag; lopende beursdagen
worden niet meegenomen. GitHub kan geplande runs later starten.

## Uitvoering

Workflow `.github/workflows/refresh_sel_par.yml` gebruikt het bestaande
`SUPABASE_DB_URL`-secret en een GitHub-token met alleen leesrechten voor Actions.
Er is geen koppeling met TradingAgents Bridge en er worden geen berichten verzonden.

Vóór de overzetting controleert de workflow of de geplande scans van de vorige
selectiedag daadwerkelijk zijn gestart en voltooid. Ook de featurebouw,
koersopvolging en relevante avondmodellen/weekly-runs worden gecontroleerd.
Een ontbrekende, wachtende of lopende bronrun blokkeert de overzetting; de
workflow wacht maximaal twee uur en faalt daarna zichtbaar zonder datawijziging.
Een voltooide maar mislukte bronrun wordt gemeld en opgenomen in het rapport:
beschikbare gedeeltelijke selecties mogen wel mee, maar zijn geen bewijs van
een volledige scan. Nieuwe intraday- of handmatige runs die na de laatste
controle starten vallen buiten die controle; de volgende dag haalt ze alsnog op.

`public.ververs_sel_par` voert de databasewijzigingen atomair uit. Een
transactieslot voorkomt gelijktijdige verversingen. `public.sel_par_runs`
registreert de succesvol verwerkte selectiegrens en de bronrunstatussen.
Een herhaling voor dezelfde grens wordt overgeslagen. Handmatige uitvoering
met `force=true` kan dezelfde dag opnieuw verwerken, met dezelfde broncontrole.

## Samenvoeging en herkomst

- Bron: `selecties`, `generieke_technicals`, `generieke_fundamentals`, `forward_returns`.
- Per strategie blijft de vroegste selectie (`created_at`, daarna `id`) leidend.
  Herhaalde bronselecties blijven in `selectie_dubbele_bronrijen` van
  `selecties_onderzoek_strategie_bron` bewaard.
- De strategiebron wordt tijdens elke verversing bijgewerkt. De hoofdtabel voegt
  strategieën samen. Gelijke niet-lege waarden worden eenmaal opgeslagen;
  ontbrekende waarden kunnen uit andere strategieën komen. Verschillende
  niet-lege waarden worden NULL en staan in `conflicterende_kolommen`.
  `strategie_bron_ids` verwijst naar de volledige bronwaarden.
- Ook oudere selectiedatums worden opnieuw verwerkt om nieuw beschikbare
  5/10/20/30/60-daagse koersen en rendementen aan te vullen.
- De verversing verwijdert geen bestaande historische onderzoekrijen en schrijft
  niets naar de vier oorspronkelijke brontabellen.
- Een wijziging in het selecties-schema stopt de verversing: de expliciete mapping
  moet dan worden bijgewerkt zodat nieuwe parameters niet stilzwijgend ontbreken.

## Grenzen voor onderzoek

De vervolgkoersen zijn uit `forward_returns` gekopieerd, niet opnieuw opgehaald.
De bron bewaart geen werkelijke vervolgkoersdatums: hun horizons zijn niet door
deze overzetting onafhankelijk geverifieerd. Rendementen gebruiken de opgeslagen
selectiekoers; bij conflicten blijven de betreffende hoofdkolommen leeg.
`fundamentals_tijdig_bekend` is alleen de bestaande controle op dagniveau.
De historische beschikbaarheid van alle overige kenmerken is niet gegarandeerd.
Gebruik toekomstige koersen, rendementen, bronmetadata en modeluitkomsten niet
automatisch als invoer voor regressie. De tabel voert zelf geen regressie uit.

De databasestructuur en functie staan in
`maintenance/sql/20261122_sel_par_refresh.sql`; de bestaande onderzoekstabellen
moeten aanwezig zijn voordat deze migratie wordt toegepast.

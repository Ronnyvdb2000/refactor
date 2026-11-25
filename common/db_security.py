"""Afscherming van tabellen die door code in het schema `public` worden aangemaakt.

Supabase geeft nieuwe tabellen standaard rechten aan de rollen `anon` en
`authenticated`. Zonder Row Level Security (RLS) is zo'n tabel via de Data API
leesbaar en wijzigbaar voor iedereen met de publieke anon-sleutel. Deze SQL zet
RLS aan en trekt de rechten van beide rollen in. De verbinding van de workflows
(`postgres`) omzeilt RLS en blijft dus gewoon werken.

De SQL is idempotent en neemt alleen actie als dat nodig is, zodat een herhaalde
aanroep (bij elke run) geen onnodige tabelvergrendeling veroorzaakt. Op een
database zonder die rollen (lokaal, CI) wordt het intrekken overgeslagen.
"""

_RECHTEN = 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'


def lockdown_sql(table: str) -> str:
    """Geeft SQL terug die RLS aanzet en `anon`/`authenticated` alle rechten ontneemt.

    `table` is een vaste, schema-gekwalificeerde naam uit de code (nooit invoer
    van een gebruiker), bijvoorbeeld 'public.research_basket_log'.
    """
    return f'''
DO $lockdown$
DECLARE rol text;
BEGIN
  IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = '{table}'::regclass) THEN
    EXECUTE 'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY';
  END IF;
  FOREACH rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rol)
       AND has_table_privilege(rol, '{table}', '{_RECHTEN}') THEN
      EXECUTE 'REVOKE ALL ON {table} FROM ' || quote_ident(rol);
    END IF;
  END LOOP;
END
$lockdown$'''

"""Daily availability guard for stored fundamentals; not an intraday PIT claim."""

# bijgewerkt_op is timestamptz; datum is the date label stored as text.
# A later update cannot establish which values were known on an earlier day.
KNOWN_FUNDAMENTALS_SQL = """bijgewerkt_op IS NOT NULL
          AND (bijgewerkt_op AT TIME ZONE 'UTC')::date <= datum::date"""

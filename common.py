"""Tijdelijke backwards-compat shim.
Zorgt dat `import common` blijft werken tijdens de migratie naar de
`common/` package. Verwijder dit bestand zodra alle bots naar
`from common import ...` zijn omgezet.
"""
from common import *  # noqa: F401,F403

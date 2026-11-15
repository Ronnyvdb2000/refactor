"""Dekkingsnorm en diagnose per deelmarkt; geen koersrequests of publicatie."""
from collections import defaultdict


SLOWAAKSE_BRONCODES = {
    '1TAT01DE', '1SLP401E', '1TMR001E', '1GVR001E', '1SKF401E',
    '1BSL01AE', '1VIP01AE', '1GRT002E', '1PRV001E',
}


def voldoende(aantal, bruikbaar):
    return aantal == 0 or (bruikbaar > 0 and aantal - bruikbaar <= max(1, aantal // 5))


def deelmarkt(naam, ticker):
    if naam not in ('tickers_059a.txt', 'tickers_059x.txt'):
        return 'Gehele lijst'
    if ticker.endswith('.VI'):
        return 'Wenen'
    if ticker.endswith('.LJ'):
        return 'Ljubljana'
    if ticker in SLOWAAKSE_BRONCODES:
        return 'Bratislava (broncode)'
    return 'Onbekende markt'


def marktdekking(rows):
    groepen = defaultdict(list)
    for row in rows:
        groepen[(row['lijst'], row['deelmarkt'])].append(row)
    result = []
    for (naam, markt), groep in sorted(groepen.items()):
        aantal = len(groep)
        goed = sum(r['bruikbaar'] is True for r in groep)
        onbekend = sum(r['bruikbaar'] is None for r in groep)
        status = ('niet_volledig_onderzocht' if onbekend else
                  ('voldoende' if voldoende(aantal, goed) else 'onvoldoende'))
        result.append(dict(lijst=naam, deelmarkt=markt, aantal=aantal, bruikbaar=goed,
                           onbekend=onbekend, dekking=None if onbekend else goed/aantal,
                           status=status))
    return result

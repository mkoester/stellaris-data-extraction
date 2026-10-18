"""DLC release dates, keyed by Steam app id.

The game files carry no release dates. These come from the Steam store API
(`store.steampowered.com/api/appdetails?appids=<id>`, `release_date.date`), fetched
2026-10-18. Ids the store no longer lists (Symbols of Domination, Paradox Account
Sign-up Bonus, Arachnoid and Creatures of the Void portrait packs, Horizon Signal)
are left out on purpose: NULL beats a guess. Add a row per new DLC.
"""

DLC_RELEASE_DATES = {
    '492740':  '2016-07-12',  # Original Game Soundtrack
    '498870':  '2016-08-04',  # Plantoids Species Pack
    '518910':  '2016-10-20',  # Leviathans Story Pack
    '553280':  '2017-04-06',  # Utopia
    '633310':  '2017-05-09',  # Anniversary Portraits
    '642750':  '2017-09-21',  # Synthetic Dawn Story Pack
    '716670':  '2018-02-22',  # Apocalypse
    '756010':  '2017-12-07',  # Humanoids Species Pack
    '844810':  '2018-05-22',  # Distant Stars Story Pack
    '944290':  '2018-12-06',  # Megacorp
    '1045980': '2019-06-04',  # Ancient Relics Story Pack
    '1140000': '2019-10-24',  # Lithoids Species Pack
    '1140001': '2020-03-17',  # Federations
    '1341520': '2020-10-29',  # Necroids Species Pack
    '1522090': '2021-04-15',  # Nemesis
    '1749080': '2021-11-22',  # Aquatics Species Pack
    '1889490': '2022-05-12',  # Overlord
    '2115770': '2022-09-20',  # Toxoids Species Pack
    '2277860': '2023-03-14',  # First Contact Story Pack
    '2380030': '2023-05-09',  # Galactic Paragons
    '2534090': '2023-11-16',  # Astral Planes
    '2840100': '2024-05-07',  # The Machine Age
    '2863060': '2024-09-10',  # Cosmic Storms
    '2863080': '2024-10-29',  # Grand Archive
    '2863180': '2024-03-26',  # Rick The Cube Species Portrait
    '3283220': '2025-11-25',  # Infernals Species Pack
    '3417840': '2025-05-05',  # BioGenesis
    '3417850': '2025-09-22',  # Shadows of the Shroud
    '3417860': '2025-03-24',  # Stargazer Species Portrait
    '4241410': '2026-04-29',  # Vipra the Vapor Species Portrait
    '4241420': '2026-06-15',  # Nomads
}

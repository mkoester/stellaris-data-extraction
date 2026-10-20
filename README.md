# stellaris-data-extraction

Parses the game files of an installed copy of [Stellaris](https://www.paradoxinteractive.com/games/stellaris)
into a normalised SQLite database. It is the data source for
[stellaris-empire-planner](https://github.com/mkoester/stellaris-empire-planner) and
[stellaris-empire-dashboard](https://github.com/mkoester/stellaris-empire-dashboard).

Python 3.10+, standard library only.

```sh
python3 stellaris-extract.py <STELLARIS_DIR> --out stellaris.sql --sqlite stellaris.sqlite
```

## Licence

The code is licensed under the [GNU Affero General Public License v3.0](LICENSE). If you
run a modified version as a public service, you must offer its source code to its users.

> Unofficial fan project, not affiliated with Paradox Interactive. Stellaris and all game data and
> text © Paradox Interactive AB.

The extracted data, including the committed dump `sql/stellaris.sql`, is Paradox content and is not
covered by this repository's licence (see [`sql/README.md`](sql/README.md)).

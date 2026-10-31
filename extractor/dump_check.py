"""Is the committed dump fit to release? It must be this code's output from the public game.

Run before every regular release (RELEASE.md): `scripts/release-check`. The dashboard's release
workflow runs it on the extractor tag it bundles.
"""

from pathlib import Path
import re
import sys

from .schema import SCHEMA
from .steam import RELEASE_OUTPUTS

_TABLE = re.compile(r'CREATE TABLE (\w+) \((.*?)\n\);', re.S)
_META = re.compile(r"INSERT OR REPLACE INTO meta \(key, value\) VALUES \('(\w+)', '([^']*)'\);")


def tables(sql: str) -> dict[str, list[str]]:
    """Each CREATE TABLE's body as tokens, comments and layout dropped."""
    return {m.group(1): re.sub(r'--[^\n]*', '', m.group(2)).split() for m in _TABLE.finditer(sql)}


def problems(dump: str, schema: str = SCHEMA) -> list[str]:
    """Why the dump cannot be released; empty when it can."""
    have, want = tables(dump), tables(schema)
    stale = sorted(t for t in want.keys() | have.keys() if have.get(t) != want.get(t))
    meta = dict(_META.findall(dump))
    branch = meta.get('steam_branch')
    out = []
    if stale:
        out.append(f"the dump is older than the code — tables differing from extractor/schema.py: "
                   f"{', '.join(stale)}. Regenerate it (AGENTS.md).")
    if branch != 'public':
        out.append(f"the dump's game is on Steam branch {branch!r} ({meta.get('game_version')}), "
                   f"not 'public'. Regenerate it from the public game.")
    return out


def main() -> int:
    found = problems(RELEASE_OUTPUTS[0].read_text(encoding='utf-8'))
    for p in found:
        print(f"release-check: {p}", file=sys.stderr)
    if not found:
        print("release-check: sql/stellaris.sql is this code's output from the public game")
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())

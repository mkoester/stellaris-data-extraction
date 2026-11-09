"""Extraction: Nomads contracts (common/missions), for the empire dashboard's contracts tab.

A contract is a mission whose category (common/missions/mission_categories) has
`is_contract = yes`. Most take their category and times from a top-level
`inline_script = contracts/…`."""

from pathlib import Path
import sys
from .clausewitz import Parser, parse_file, tokenize
from .localisation import _clean_loc
from .values import _as_blocks, _resolve_modifier_float
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _key(v) -> str | None:
    return v.strip('"') if isinstance(v, str) else None


def contract_categories(parsed: dict) -> set[str]:
    """The mission categories that are contracts."""
    return {k for k, v in parsed.items() if isinstance(v, dict) and v.get('is_contract') == 'yes'}


def with_inline(block: dict, load) -> dict:
    """A mission's own keys over those of its top-level `inline_script = { script … PARAM … }`
    references, `$PARAM$` substituted. Nested inline scripts (in triggers, effects) stay as
    they are: only the top level carries category and times."""
    merged: dict = {}
    refs = block.get('inline_script')
    for ref in ([{'script': refs}] if isinstance(refs, str) else _as_blocks(refs)):
        text = load(str(ref.get('script', '')).strip('"'))
        if text is None:
            continue
        for k, v in ref.items():
            if k != 'script':
                text = text.replace(f'${k}$', str(v).strip('"'))
        merged.update(Parser(tokenize(text)).parse())
    merged.update({k: v for k, v in block.items() if k != 'inline_script'})
    return merged


def days(raw, variables: dict[str, float]) -> int | None:
    """A time in days: a number or an `@variable` (`@contract_time_base` = 1800)."""
    if isinstance(raw, str) and raw.startswith('@'):
        v = variables.get(raw)
    else:
        v = _resolve_modifier_float(raw)
    return int(v) if v is not None else None


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_contracts(install_dir: Path, loc: dict[str, str], load, w: Writer,
                      progress=sys.stderr, scripted_vars: dict[str, float] | None = None) -> None:
    root = install_dir / 'common' / 'missions'
    cats: set[str] = set()
    for f in sorted((root / 'mission_categories').glob('*.txt')):
        cats |= contract_categories(parse_file(f))
    n = 0
    for f in sorted((root / 'missions').glob('*.txt')):
        if f.name.startswith('99_README'):
            continue
        parsed = parse_file(f)
        variables = dict(scripted_vars or {})
        for k, v in parsed.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, data in parsed.items():
            if key.startswith('@') or not isinstance(data, dict):
                continue
            m = with_inline(data, load)
            category = _key(m.get('category'))
            if category not in cats:
                continue
            raw = loc.get(key)
            w.execute(
                "INSERT OR REPLACE INTO contract (key, name, category, time_to_accept, time_to_complete) VALUES (?, ?, ?, ?, ?)",
                (key, _clean_loc(raw, loc) if raw else None, category,
                 days(m.get('time_to_accept'), variables), days(m.get('time_to_complete'), variables)))
            n += 1
    print(f"  {n} contracts ({len(cats)} contract categories)", file=progress)

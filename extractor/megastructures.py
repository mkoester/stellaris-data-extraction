"""Extraction: megastructure stages, their build cost, upkeep and build time."""

from pathlib import Path
import sys
from .buildings import _expand_inline_refs, script_loader
from .clausewitz import parse_file
from .values import _as_blocks, _resolve_modifier_float, _resolve_var, _to_list
from .writer import Writer

# The only cost gate kept: the bio-ship variant. `NOT = { has_country_flag = … }` is a one-off
# discount (4.5.3 hyper relay: the Flash Forge's) and read as the flag being absent.
_BIO = 'country_uses_bio_ships'


def _ships(trigger) -> str | None:
    """A cost block's gate → 'any' | 'bio' | 'regular', or None when it gates on anything else."""
    if trigger is None:
        return 'any'
    if not isinstance(trigger, dict):
        return None
    rest = {k: v for k, v in trigger.items()
            if not (k == 'NOT' and isinstance(v, dict) and set(v) == {'has_country_flag'})}
    if not rest:
        return 'any'
    if set(rest) == {_BIO} and rest[_BIO] in ('yes', 'no'):
        return 'bio' if rest[_BIO] == 'yes' else 'regular'
    return None


def megastructure_resources(mdef: dict, variables: dict[str, float], load_script=None
                            ) -> tuple[list[tuple[str, str, str, float]], int]:
    """A megastructure's resources (its own and its inline scripts': 4.5.3 arkships declare
    theirs only in `megastructures/arkship`) → ([(kind, resource, ships, amount)], skipped),
    cost and upkeep, each block's `mult` applied and blocks of one gate summed (bio arkship:
    5000 alloys + 17 500 food). `skipped` counts the blocks gated on anything but the bio-ship
    variant (a subject's expansion term), which are left out."""
    acc: dict[tuple[str, str, str], float] = {}
    skipped = 0
    for src in (mdef, *_expand_inline_refs(mdef, load_script)):
        for res in _as_blocks(src.get('resources')):
            for kind in ('cost', 'upkeep'):
                for block in _as_blocks(res.get(kind)):
                    ships = _ships(block.get('trigger'))
                    if ships is None:
                        skipped += 1
                        continue
                    mult = _resolve_var(block.get('mult', 1), variables)
                    for resource, raw in block.items():
                        if resource in ('trigger', 'mult'):
                            continue
                        amt = _resolve_var(raw, variables)
                        if amt is None or mult is None:
                            continue
                        k = (kind, resource, ships)
                        acc[k] = acc.get(k, 0.0) + amt * mult
    return [(kind, res, ships, amt) for (kind, res, ships), amt in sorted(acc.items())], skipped


def _build_days(mdef: dict, variables: dict[str, float], load_script=None) -> float | None:
    """A megastructure's `build_time` in days (its own, else an inline script's), before modifiers."""
    for src in (mdef, *_expand_inline_refs(mdef, load_script)):
        days = _resolve_var(src.get('build_time'), variables)
        if days is not None:
            return days
    return None


def extract_megastructures(directory: Path, scripted_vars: dict[str, float],
                           inline_scripts_dir: Path, w: Writer, verbose: bool) -> None:
    """Every megastructure stage of `directory` → `megastructure` (one row per `upgrade_from`
    stage, NULL for a first stage), `megastructure_resource` and `build_time`. Per file, as
    `@variables` resolve per file."""
    load = script_loader(inline_scripts_dir)
    n = skipped = 0
    for f in sorted(directory.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception as e:
            print(f"  WARNING: failed to parse {f.name}: {e}", file=sys.stderr)
            continue
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, mdef in sorted(data.items()):
            if key.startswith('@') or not isinstance(mdef, dict):
                continue
            prev = sorted({p for p in _to_list(mdef.get('upgrade_from')) if isinstance(p, str)}) or [None]
            for p in prev:
                w.execute("INSERT OR REPLACE INTO megastructure (key, upgrade_from) VALUES (?, ?)",
                          (key, p))
            rows, s = megastructure_resources(mdef, variables, load)
            skipped += s
            for kind, resource, ships, amt in rows:
                w.execute("INSERT OR REPLACE INTO megastructure_resource "
                          "(key, kind, resource, ships, amount) VALUES (?, ?, ?, ?, ?)",
                          (key, kind, resource, ships, amt))
                n += 1
            days = _build_days(mdef, variables, load)
            if days is not None:
                w.execute("INSERT OR REPLACE INTO build_time (key, days) VALUES (?, ?)", (key, days))
    w.commit()
    if verbose:
        print(f"  megastructure_resource: {n} rows, {skipped} otherwise gated blocks left out")

"""Extraction: the Shroud's patrons (position, colour, accords, covenant), their callings with
each tier's attunement reward, and the deeds with theirs. For the dashboard, which reads a
country's patron relations from saves by key."""

from pathlib import Path
import sys
from .modifiers import _block_modifier_values, _write_modifier_label
from .traditions import _Ctx, _load
from .triggers import _scripted_trigger_index, dlc_trigger_map
from .values import _as_blocks, _resolve_var, _to_list
from .writer import Writer


# ── Pure helpers ───────────────────────────────────────────────────────────────

def rgb_hex(color) -> str | None:
    """`color = { r g b a }` with 0–1 floats → '#rrggbb'; None if it is not such a list."""
    parts = _to_list(color)
    try:
        r, g, b = (float(str(x)) for x in parts[:3])
    except (ValueError, TypeError):
        return None
    if len(parts) < 3:
        return None
    return '#' + ''.join(f'{max(0, min(255, round(v * 255))):02x}' for v in (r, g, b))


def calling_tiers(data: dict, variables: dict[str, float]) -> list[tuple[int, float | None]]:
    """A calling's `tier` blocks → [(tier number, attunement reward)], numbered from
    `first_tier` (default 1)."""
    first = int(float(str(data.get('first_tier', 1))))
    return [(first + n, _resolve_var(t.get('attunement'), variables)
             if t.get('attunement') is not None else None)
            for n, t in enumerate(_as_blocks(data.get('tier')))]


def position(data: dict) -> tuple[float | None, float | None]:
    p = data.get('position') if isinstance(data.get('position'), dict) else {}
    try:
        return float(str(p.get('x'))), float(str(p.get('y')))
    except (ValueError, TypeError):
        return None, None


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_shroud(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                   w: Writer, progress=sys.stderr) -> None:
    common = install_dir / 'common' / 'patrons'
    ctx = _Ctx(loc, dlc_trigger_map(_scripted_trigger_index(install_dir)), w)

    patrons = _load(common, scripted_vars)
    n_accords = 0
    for order, (key, (data, pvars, _)) in enumerate(patrons.items()):
        x, y = position(data)
        cov = next(iter(_as_blocks(data.get('covenant'))), {})
        w.execute(
            """INSERT INTO patron (key, name, category, x, y, color, covenant, covenant_desc,
               sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, ctx.text(key), data.get('category') if isinstance(data.get('category'), str)
             else None, x, y, rgb_hex(data.get('color')),
             ctx.text(cov.get('name')) if isinstance(cov.get('name'), str) else None,
             ctx.text(cov.get('desc'), True, pvars) if isinstance(cov.get('desc'), str) else None,
             order))
        for kind in ('passive', 'active'):
            for a in _as_blocks(data.get(f'{kind}_accord')):
                name = a.get('name') if isinstance(a.get('name'), str) else None
                if not name:
                    continue
                w.execute(
                    """INSERT INTO patron_accord (patron_key, kind, key, name, sort_order)
                       VALUES (?, ?, ?, ?, ?)""", (key, kind, name, ctx.text(name), n_accords))
                n = 0
                for b in _as_blocks(a.get('triggered_modifier')):
                    for mk, mv in _block_modifier_values(b, pvars):
                        w.execute(
                            """INSERT INTO patron_accord_modifier
                               (accord_key, modifier_key, modifier_value, sort_order)
                               VALUES (?, ?, ?, ?)""", (name, mk, mv, n))
                        _write_modifier_label(w, loc, mk)
                        n += 1
                n_accords += 1
        for n, c in enumerate(x for x in _to_list(data.get('callings')) if isinstance(x, str)):
            w.execute("INSERT INTO patron_calling_of (patron_key, calling_key, sort_order) "
                      "VALUES (?, ?, ?)", (key, c, n))
        for n, d in enumerate(x for x in _to_list(data.get('deeds')) if isinstance(x, str)):
            w.execute("INSERT INTO patron_deed_of (patron_key, deed_key, sort_order) "
                      "VALUES (?, ?, ?)", (key, d, n))

    callings = _load(common / 'callings', scripted_vars)
    for order, (key, (data, cvars, _)) in enumerate(callings.items()):
        w.execute("INSERT INTO calling (key, name, description, sort_order) VALUES (?, ?, ?, ?)",
                  (key, ctx.text(key), ctx.text(f'{key}_desc', True, cvars), order))
        for tier, att in calling_tiers(data, cvars):
            w.execute(
                """INSERT INTO calling_tier (calling_key, tier, attunement, trigger_desc)
                   VALUES (?, ?, ?, ?)""",
                (key, tier, att, ctx.text(f'{key}_tier{tier}_trigger_desc', True, cvars)))

    deeds = _load(common / 'deeds', scripted_vars)
    for order, (key, (data, dvars, _)) in enumerate(deeds.items()):
        att = _resolve_var(data.get('attunement'), dvars) if data.get('attunement') is not None else None
        w.execute(
            """INSERT INTO deed (key, name, description, attunement, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (key, ctx.text(key), ctx.text(f'{key}_desc', True, dvars), att, order))

    print(f"  {len(patrons)} patrons, {n_accords} accords, {len(callings)} callings, "
          f"{len(deeds)} deeds", file=progress)
    w.commit()

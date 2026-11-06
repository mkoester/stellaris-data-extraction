"""Extraction: what each technology unlocks, as the game's research card lists it.

Two sources, as on the card:
- objects naming the tech in their `prerequisites` — buildings, districts, component sets, ship
  sizes and armies, which the engine lists by itself ("Unlocks Building: Field Dynamics Center");
  a tech's `hide_prereq_for_desc = component|ship` hides that kind, as in the game;
- the tech's own `prereqfor_desc` lines (`custom`, `ship`, `component`, `diplo_action`; kind
  `line`), whose localised titles carry their own prefix ("Unlocks Megastructure: Dyson Swarm");
- its `feature_flags` ("Unlocks Feature: Auto-Research Technologies"), named `feature_<flag>`.
A derived object an own line already names (Science Ship) is left out, as the card has it once.
Variants for other kinds of empire stay in (bio-ship components, the habitat's or the nomads'
districts): their `potential` is a planet- or ship-scoped trigger that no empire-wide view decides.
Megastructures, edicts and deposits also name techs as prerequisites, but the game shows them only
through such lines, so they are not derived here (a derived row would double the line).
"""

from .localisation import _clean_loc
from .writer import Writer

# The kinds the engine derives from `prerequisites`, in the order they are listed.
DERIVED_KINDS = ('building', 'district', 'component', 'ship', 'army')

# A feature flag the engine names itself rather than through `feature_<flag>` (4.5.3).
_FEATURE_LOC = {'tech_automated_exploration': 'AUTO_EXPLORE_ORDER'}


def tech_refs(prereq) -> list[str]:
    """The tech keys a `prerequisites` value names, nested OR/AND groups included."""
    if isinstance(prereq, str):
        p = prereq.strip('"')
        return [p] if p.startswith('tech_') else []
    if isinstance(prereq, list):
        return [t for v in prereq for t in tech_refs(v)]
    if isinstance(prereq, dict):
        return [t for v in prereq.values() for t in tech_refs(v)]
    return []


def objects_of(parsed: dict):
    """(key, block) per object of one parsed file. Component templates repeat one top-level key
    (`weapon_component_template = { key = "…" }`), which the parser promotes to a list: those are
    named by their own `key`."""
    for top, value in parsed.items():
        if top.startswith('@'):
            continue
        for block in value if isinstance(value, list) else [value]:
            if isinstance(block, dict):
                key = block.get('key')
                yield (str(key).strip('"') if isinstance(key, str) else top), block


def _hidden(tech: dict) -> set[str]:
    h = tech.get('hide_prereq_for_desc')
    return {str(x) for x in (h if isinstance(h, list) else [h]) if x is not None}


def _name(loc: dict[str, str], *keys: str) -> str | None:
    for k in keys:
        if k and loc.get(k):
            return _clean_loc(loc[k], loc)
    return None


def _flags(tech: dict) -> list[str]:
    f = tech.get('feature_flags')
    return [str(x).strip('"') for x in (f if isinstance(f, list) else [f] if f else [])]


def unlock_rows(technologies: dict, sources: dict[str, list[dict]],
                loc: dict[str, str]) -> list[tuple]:
    """Rows (tech_key, kind, item_key, label, detail, sort_order). `sources`: derived kind → its
    parsed files. Per tech: its own lines first (file order), its features, then the derived
    objects by kind and label."""
    techs = {k: v for k, v in technologies.items()
             if isinstance(v, dict) and not k.startswith('@')}
    derived: dict[str, dict[tuple[str, str], str]] = {}
    for kind in DERIVED_KINDS:
        for parsed in sources.get(kind, []):
            for key, block in objects_of(parsed):
                refs = tech_refs(block.get('prerequisites'))
                if not refs:
                    continue
                if kind == 'component':
                    item = str(block.get('component_set') or key).strip('"')
                    label = _name(loc, item, key)
                else:
                    item, label = key, _name(loc, key)
                for t in sorted(set(refs)):
                    if t in techs and kind not in _hidden(techs[t]):
                        derived.setdefault(t, {}).setdefault((kind, item), label or item)
    rows: list[tuple] = []
    for t in sorted(techs):
        order = 0
        own = techs[t].get('prereqfor_desc')
        for entries in (own.values() if isinstance(own, dict) else []):
            for e in entries if isinstance(entries, list) else [entries]:
                if not isinstance(e, dict) or not isinstance(e.get('title'), str):
                    continue
                title = e['title'].strip('"')
                desc = e.get('desc').strip('"') if isinstance(e.get('desc'), str) else None
                rows.append((t, 'line', title, _name(loc, title) or title,
                             _name(loc, desc) if desc else None, order))
                order += 1
        own_labels = [r[3] for r in rows if r[0] == t]
        for flag in _flags(techs[t]):
            label = _name(loc, _FEATURE_LOC.get(flag, ''), f'feature_{flag}')
            if label:
                rows.append((t, 'feature', flag, label, _name(loc, f'feature_{flag}_desc'), order))
                order += 1
        for (kind, item), label in sorted(
                derived.get(t, {}).items(),
                key=lambda kv: (DERIVED_KINDS.index(kv[0][0]), kv[1], kv[0][1])):
            if any(label in own for own in own_labels):
                continue
            rows.append((t, kind, item, label, None, order))
            order += 1
    return rows


def extract_tech_unlocks(technologies: dict, sources: dict[str, list[dict]],
                         loc: dict[str, str], w: Writer, verbose: bool) -> None:
    rows = unlock_rows(technologies, sources, loc)
    for r in rows:
        w.execute("INSERT OR REPLACE INTO technology_unlock (tech_key, kind, item_key, label, detail, sort_order) VALUES (?, ?, ?, ?, ?, ?)", r)
    if verbose:
        print(f"  {len(rows)} tech unlocks")

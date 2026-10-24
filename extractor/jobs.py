"""Extraction: pop jobs, job resources/modifiers, conditional job swaps."""

from pathlib import Path
import sys
from .clausewitz import parse_file
from .localisation import _clean_loc
from .modifiers import _block_modifier_values, _write_modifier_label
from .triggers import _collect_swap_sources, _scripted_trigger_index, _signals
from .values import _as_blocks, _resolve_modifier_float
from .writer import Writer


# Sub-keys inside a job produces/upkeep block that are not resources (skip them).
_JOB_RES_SKIP = {'trigger', 'modifier', 'mult', 'category', 'pop_amount_change_category',
                 'pop_resources', 'desc', 'multiplier'}


def _block_numeric(block, variables: dict[str, float]) -> list[tuple[str, float]]:
    """[(resource, amount)] — the direct numeric resource entries of one produces/upkeep
    block (resolving @variables), skipping trigger/modifier/category sub-blocks."""
    if not isinstance(block, dict):
        return []
    out: list[tuple[str, float]] = []
    for k, v in block.items():
        if k in _JOB_RES_SKIP or isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        val = variables.get(s) if s.startswith('@') else _resolve_modifier_float(v)
        if val is not None and val != 0:
            out.append((k, val))
    return out


def _is_scaled(block: dict) -> bool:
    """A block whose output is scaled by a script value (`mult = value:…`)."""
    return str(block.get('mult', '')).startswith('value:')


def _drop_scaled_duplicates(blocks: list) -> list:
    """Drop unconditional script-value-scaled blocks whose resources an unconditional plain
    block already gives — a stockpile-scaled bonus on top of a base output (polytechnic),
    not the base itself. A scaled block that is the only source (knights) is kept."""
    plain = {k for b in blocks if isinstance(b, dict) and 'trigger' not in b and not _is_scaled(b)
             for k in b}
    return [b for b in blocks
            if not (isinstance(b, dict) and 'trigger' not in b and _is_scaled(b)
                    and set(b) - {'mult'} <= plain)]


# Condition tokens that split empires into types, each paying its own variant.
_EMPIRE_TYPE_TOKENS = ('is_archetype=', 'has_trait=', 'has_authority=')

# The condition token a gestalt (hive / machine) empire guarantees.
_GESTALT_TOKEN = 'has_ethic=ethic_gestalt_consciousness'


def _fallback_block(blocks: list, signals) -> dict | None:
    """When every block is gated and none applies, the variant to show if they are
    alternatives one of which every empire pays: the first branch of an explicit `= no`
    complement (servitor / not), else the first of two or more blocks gated purely on
    empire type (organic/lithoid/infernal, hive/machine). Independent extras — a single
    gated block (Pleasure Seekers' civilian upkeep) or civic/flag bonuses (enforcer
    unity) — give None. `signals` maps a trigger to its (positive, negative) tokens."""
    dicts = [b for b in blocks if isinstance(b, dict)]
    if len(dicts) < 2 or not all('trigger' in b for b in dicts):
        return None
    sigs = [signals(b['trigger']) for b in dicts]
    if any(neg for _, neg in sigs):
        return dicts[0]
    typed = [b for b, (pos, _) in zip(dicts, sigs)
             if pos and all(t.startswith(_EMPIRE_TYPE_TOKENS) for t in pos)]
    return typed[0] if len(typed) >= 2 else None


def _collect_res_dict(resd, load_script, out: dict[str, list]) -> None:
    """Accumulate produces/upkeep blocks from a `resources` dict into `out`, following any
    `inline_script` it references (a job's resources can be nested two levels deep:
    job-level inline_script → resources → resources.inline_script → produces/upkeep)."""
    if not isinstance(resd, dict):
        return
    for kind in ('produces', 'upkeep'):
        for b in _as_blocks(resd.get(kind)):
            if isinstance(b, dict):
                out[kind].append(b)
    isc = resd.get('inline_script')
    for sn in (isc if isinstance(isc, list) else [isc]):
        if isinstance(sn, str):
            _collect_res_dict(load_script(sn), load_script, out)


def _job_resource_blocks(jdef: dict, load_script) -> dict[str, list]:
    """All of a job's produces/upkeep blocks {'produces': [...], 'upkeep': [...]}, following
    both its top-level `resources` block and any job-level `inline_script` (jobs/job_*) that
    carries a `resources` block — so jobs whose figures live in shared inline scripts
    (enforcer/telepath/foundry/…) are fully resolved, conditional blocks included."""
    out: dict[str, list] = {'produces': [], 'upkeep': []}
    seen: set[str] = set()

    def follow(node: dict) -> None:
        _collect_res_dict(node.get('resources'), load_script, out)
        isc = node.get('inline_script')
        for sn in (isc if isinstance(isc, list) else [isc]):
            if isinstance(sn, str) and sn not in seen:   # job_telepath → job_telepath_resources
                seen.add(sn)
                follow(load_script(sn))

    follow(jdef)
    return out


def _names(v) -> list:
    """A key that may hold one value or, repeated, a list of them."""
    return v if isinstance(v, list) else [v]


def _job_budget_source(jdef: dict, load_script) -> str | None:
    """The budget source a job's output is booked under — its `resources.category`
    (`planet_technician` for `technician_drone` too: drone jobs reuse the organic jobs'
    categories), following resources- and job-level inline scripts as
    `_job_resource_blocks` does. None when the job has no resources block."""
    seen: set[str] = set()

    def find(node) -> str | None:
        if not isinstance(node, dict):
            return None
        res = node.get('resources')
        if isinstance(res, dict):
            if isinstance(res.get('category'), str):
                return res['category']
            for sn in _names(res.get('inline_script')):
                if isinstance(sn, str) and (cat := find({'resources': load_script(sn)})):
                    return cat
        for sn in _names(node.get('inline_script')):
            if isinstance(sn, str) and sn not in seen:
                seen.add(sn)
                if cat := find(load_script(sn)):
                    return cat
        return None

    return find(jdef)


def extract_jobs(install_dir: Path, loc: dict[str, str],
                 scripted_vars: dict[str, float], inline_scripts_dir: Path,
                 w: Writer, verbose: bool) -> None:
    """Extract common/pop_jobs → job (+ job_resource): every job's category, flavour
    description and base produced/upkeep resources. Many jobs put their resources in an
    `output/*` inline script (foundry/researcher/bureaucrat/…) — those are expanded; the
    figures are the base (conditional bonuses and job swaps are not unrolled)."""
    jobs_dir = install_dir / 'common' / 'pop_jobs'
    if not jobs_dir.is_dir():
        print(f"  WARNING: {jobs_dir} not found", file=sys.stderr)
        return

    idx = _scripted_trigger_index(install_dir)
    script_cache: dict[str, dict] = {}

    def load_script(name: str) -> dict:
        if name not in script_cache:
            p = inline_scripts_dir / (name + '.txt')
            try:
                script_cache[name] = parse_file(p) if p.exists() else {}
            except Exception:
                script_cache[name] = {}
        return script_cache[name]

    order = 0
    for f in sorted(jobs_dir.glob('*.txt')):
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
        for key, jdef in data.items():
            if key.startswith('@') or not isinstance(jdef, dict):
                continue
            category = jdef.get('category')
            category = category if isinstance(category, str) else None
            raw_name = loc.get('job_' + key)
            name = _clean_loc(raw_name, loc) if raw_name else None
            raw_desc = loc.get('job_' + key + '_desc')
            desc = _clean_loc(raw_desc, loc, keep_newlines=True) if raw_desc else None
            w.execute(
                """INSERT OR REPLACE INTO job (key, name, category, description, budget_source, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, name, category, desc, _job_budget_source(jdef, load_script), order))
            order += 1
            # Base-empire figures: unconditional blocks plus those whose trigger a plain
            # empire meets, following resources- and job-level inline scripts alike.
            blocks = {k: _drop_scaled_duplicates(v)
                      for k, v in _job_resource_blocks(jdef, load_script).items()}
            # Drone jobs only exist in gestalt empires, so evaluate them as one.
            state = {_GESTALT_TOKEN} if (category or '').endswith('_drone') else set()
            res_rows = _eval_job_resources(blocks, state, idx, variables)
            for kind in ('produces', 'upkeep'):
                rows = [(r, a) for k, r, a in res_rows if k == kind]
                if not rows:
                    rows = _block_numeric(
                        _fallback_block(blocks[kind], lambda t: _signals(t, idx)), variables)
                for r_order, (rk, rv) in enumerate(rows):
                    w.execute(
                        """INSERT OR IGNORE INTO job_resource
                               (job_key, kind, resource, amount, sort_order)
                           VALUES (?, ?, ?, ?, ?)""",
                        (key, kind, rk, rv, r_order))
            # Non-resource effects (Amenities, Crime, Stability, …): base-empire state.
            mod_blocks = _collect_modifier_blocks(jdef, load_script)
            for m_order, (mk, mv) in enumerate(_eval_job_modifiers(mod_blocks, set(), idx, variables)):
                w.execute(
                    """INSERT OR IGNORE INTO job_modifier (job_key, modifier, value, sort_order)
                       VALUES (?, ?, ?, ?)""",
                    (key, mk, mv, m_order))
                _write_modifier_label(w, loc, mk)
    w.commit()
    if verbose:
        print(f"  jobs: {order}")


def _eval_job_resources(blocks: dict[str, list], swap_pos: set[str], idx: dict[str, dict],
                        variables: dict[str, float]) -> list[tuple[str, str, float]]:
    """Sum a job's produces/upkeep for the empire state a swap implies — `swap_pos` is the
    set of positive condition tokens the swap guarantees (empty = plain base empire). A
    conditional block applies iff all its positive requirements are guaranteed
    (⊆ swap_pos) and none of its negative requirements are contradicted by a guaranteed
    positive (so the `= no` default and empire-specific is_robot_empire variants drop out)."""
    out: dict[tuple[str, str], float] = {}
    for kind in ('produces', 'upkeep'):
        for block in blocks.get(kind, []):
            if not isinstance(block, dict):
                continue
            bt = block.get('trigger')
            if bt is not None:
                bpos, bneg = _signals(bt, idx)
                if not bpos <= swap_pos or (bneg & swap_pos):
                    continue
            for k, v in block.items():
                if k == 'trigger' or isinstance(v, (dict, list)):
                    continue
                s = str(v).strip()
                val = variables.get(s) if s.startswith('@') else _resolve_modifier_float(v)
                if val is not None and val != 0:
                    out[(kind, k)] = round(out.get((kind, k), 0.0) + val, 4)
    return [(kind, resource, amt) for (kind, resource), amt in out.items() if amt != 0]


def _collect_modifier_blocks(jdef: dict, load_script, seen: set[str] | None = None
                             ) -> list[tuple[object, dict]]:
    """[(potential_or_None, block)] for a job: planet_modifier / country_modifier (always-on,
    potential=None) and triggered_planet_modifier (gated by its `potential`), following the
    job-level inline_script chain (recursively, e.g. job_enforcer → job_enforcer_additional…)."""
    if seen is None:
        seen = set()
    blocks: list[tuple[object, dict]] = []
    for key in ('planet_modifier', 'country_modifier'):
        for b in _as_blocks(jdef.get(key)):
            blocks.append((None, b))
    for b in _as_blocks(jdef.get('triggered_planet_modifier')):
        blocks.append((b.get('potential'), b))
    isc = jdef.get('inline_script')
    for sn in (isc if isinstance(isc, list) else [isc]):
        if isinstance(sn, str) and sn not in seen:
            seen.add(sn)
            blocks += _collect_modifier_blocks(load_script(sn), load_script, seen)
    return blocks


# Amenity modifiers are stored in the ×100 pop/workforce space (clerk = 300 → +3 Amenities),
# unlike crime/stability/defense-armies which are already per-pop — so scale only these down.
_MOD_X100 = {'planet_amenities_add', 'planet_amenities_no_happiness_add'}


def _block_mult(block: dict, variables: dict[str, float]) -> float:
    """The block-level `mult` as a number for base evaluation. A literal/@var resolves
    directly; `value:scripted_modifier_mult|…` is a base-1.0 multiplier (1 + empire bonuses,
    0 at base); any other scope ref (e.g. planet.modifier:… → 0 by default) zeroes the block."""
    if 'mult' not in block:
        return 1.0
    raw = block['mult']
    s = str(raw).strip()
    f = variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)
    if f is not None:
        return f
    if 'scripted_modifier_mult' in s:
        return 1.0
    return 0.0


def _eval_job_modifiers(blocks: list[tuple[object, dict]], swap_pos: set[str],
                        idx: dict[str, dict], variables: dict[str, float]
                        ) -> list[tuple[str, float]]:
    """Sum a job's modifier effects for the empire state `swap_pos` implies (empty = base).
    Same gating as resources: a potential-gated block applies iff its positives are
    guaranteed and no negative is contradicted. The block-level `mult` scales its values
    (a scope ref that's 0 at base drops the block)."""
    out: dict[str, float] = {}
    for potential, block in blocks:
        if potential is not None:
            bpos, bneg = _signals(potential, idx)
            if not bpos <= swap_pos or (bneg & swap_pos):
                continue
        scale = _block_mult(block, variables)
        if scale == 0:
            continue
        for k, v in _block_modifier_values(block, variables):
            out[k] = round(out.get(k, 0.0) + v * scale, 4)
    return [(k, round(v / 100, 4) if k in _MOD_X100 else v)
            for k, v in out.items() if v != 0]


def extract_job_swaps(install_dir: Path, loc: dict[str, str], scripted_vars: dict[str, float],
                      inline_scripts_dir: Path, w: Writer, verbose: bool) -> None:
    """Per base job, each `swap_type` variant + its trigger → which civic/origin forces
    the swap (resolved through scripted_triggers) → job_swap, plus the swapped job's
    effective resources (base resources under the swap condition) → job_swap_resource."""
    jobs_dir = install_dir / 'common' / 'pop_jobs'
    if not jobs_dir.is_dir():
        return
    idx = _scripted_trigger_index(install_dir)
    script_cache: dict[str, dict] = {}

    def load_script(name: str) -> dict:
        if name not in script_cache:
            p = inline_scripts_dir / (name + '.txt')
            try:
                script_cache[name] = parse_file(p) if p.exists() else {}
            except Exception:
                script_cache[name] = {}
        return script_cache[name]

    def job_name(jkey: str):
        raw = loc.get('job_' + jkey)
        return _clean_loc(raw, loc) if raw else None

    order = 0
    for f in sorted(jobs_dir.glob('*.txt')):
        try:
            data = parse_file(f)
        except Exception:
            continue
        variables = dict(scripted_vars)
        for k, v in data.items():
            if k.startswith('@'):
                fv = _resolve_modifier_float(v)
                if fv is not None:
                    variables[k] = fv
        for key, jdef in data.items():
            if key.startswith('@') or not isinstance(jdef, dict):
                continue
            sd = jdef.get('swappable_data')
            swaps = _as_blocks(sd.get('swap_type')) if isinstance(sd, dict) else []
            swaps += _as_blocks(jdef.get('swap_type'))
            if not swaps:
                continue
            # A drone (gestalt) base job. The catalytic-style OR triggers list all four
            # civic variants, so without this a regular civic would also pick up the
            # machine/hive drone swaps — keep only gestalt-matching civic attributions.
            cat = jdef.get('category')
            job_gestalt = isinstance(cat, str) and 'drone' in cat

            def attribute_ok(source_kind: str, source_key: str) -> bool:
                if source_kind != 'civic':
                    return True
                civic_gestalt = 'machine' in source_key or 'hive' in source_key
                return civic_gestalt == job_gestalt
            # All of the base job's produces/upkeep blocks, following the full inline-script
            # chain (jobs whose figures live in shared inline scripts, possibly nested). The
            # base side of the tooltip is computed from the same blocks (plain empire) so it
            # is correct even when the reference job_resource row is empty/missing.
            merged = _job_resource_blocks(jdef, load_script)
            mod_blocks = _collect_modifier_blocks(jdef, load_script)
            base_res = _eval_job_resources(merged, set(), idx, variables)
            base_mod = _eval_job_modifiers(mod_blocks, set(), idx, variables)
            for st in swaps:
                if not isinstance(st, dict):
                    continue
                swapped = st.get('name')
                if not isinstance(swapped, str):
                    continue
                trig = st.get('trigger')
                sources = _collect_swap_sources(trig, idx)
                if not sources:
                    continue
                swap_pos, _ = _signals(trig, idx)
                swap_res = _eval_job_resources(merged, swap_pos, idx, variables)
                swap_mod = _eval_job_modifiers(mod_blocks, swap_pos, idx, variables)
                # Sorted: set order varies per run (hash seed) and would make dumps
                # nondeterministic — spurious diffs in stellaris-data-comparison.
                for kind, skey in sorted(sources):
                    if not attribute_ok(kind, skey):
                        continue
                    w.execute(
                        """INSERT OR IGNORE INTO job_swap
                               (source_kind, source_key, base_job, base_name,
                                swapped_job, swapped_name, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (kind, skey, key, job_name(key), swapped, job_name(swapped), order))
                    order += 1
                    for side, rows in (('base', base_res), ('swapped', swap_res)):
                        for rk, resource, amt in rows:
                            w.execute(
                                """INSERT OR IGNORE INTO job_swap_resource
                                       (source_key, base_job, swapped_job, side, kind, resource, amount)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (skey, key, swapped, side, rk, resource, amt))
                    for side, rows in (('base', base_mod), ('swapped', swap_mod)):
                        for mk, mv in rows:
                            w.execute(
                                """INSERT OR IGNORE INTO job_swap_modifier
                                       (source_key, base_job, swapped_job, side, modifier, value)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (skey, key, swapped, side, mk, mv))
                            _write_modifier_label(w, loc, mk)
    w.commit()
    if verbose:
        print(f"  job swaps: {order}")

"""Localisation loading and text cleanup ($KEY$ refs, £icons£, [commands], §codes§)."""

from pathlib import Path
import re


_LOC_INLINE_REF = re.compile(r'\$(\w+)\$')
_LOC_FMT_CODE   = re.compile(r'§.')          # §X / §! colour codes
_LOC_ICON       = re.compile(r'£(\w+?)(?:\|\w+)?£')  # £icon£ (or £icon|frame£)
# Scripted-loc concept tag: ['concept_key'], ['type:key'] (e.g. ['technology:tech_x'],
# ['building:building_x']), each optionally with ', inline display text'.
_LOC_CONCEPT    = re.compile(r"\['([\w:]+)'(?:,\s*([^\]]*))?\]")

def _concept_sub(m: re.Match) -> str:
    """['concept_x'] / ['type:key'] → the inline text if given, else a $key$ ref the
    expansion loop resolves (['technology:tech_psionic_aura'] → $tech_psionic_aura$)."""
    token, inline = m.group(1), m.group(2)
    if inline:
        return inline
    key = token.split(':', 1)[1] if ':' in token else token
    return f'${key}$'
# A job-scope name command, e.g. [foundry.GetNamePlural] — resolvable to the job's loc
# name ("Metallurgists"). Used in swap tooltips ("X are replaced with Y").
_LOC_SCOPE_NAME = re.compile(r'\[(\w+)\.GetName(Plural)?(?:WithIcon)?\]')
# A bare scripted-loc command, e.g. [GetResearcherPluralWithIcon] (common/scripted_loc).
_LOC_SCRIPTED   = re.compile(r'\[(\w+)\]')
# Scripted-loc command → the loc key it shows for a regular empire, for the commands that
# name a job (set once per run by set_scripted_loc; empty = none resolved).
_SCRIPTED_LOC: dict[str, str] = {}


def scripted_loc_jobs(defined_texts: list[dict]) -> dict[str, str]:
    """Job-naming scripted-loc commands → loc key. The choice is per empire type at runtime;
    take `default`, else the first variant (the regular empire's). Pure."""
    out: dict[str, str] = {}
    for d in defined_texts:
        name = d.get('name')
        if not isinstance(name, str):
            continue
        key = d.get('default')
        if not isinstance(key, str):
            texts = [t for t in (d.get('text') if isinstance(d.get('text'), list)
                                 else [d.get('text')]) if isinstance(t, dict)]
            key = next((t['localization_key'] for t in texts
                        if isinstance(t.get('localization_key'), str)), None)
        if isinstance(key, str) and key.startswith('job_'):
            out[name] = key
    return out


def set_scripted_loc(mapping: dict[str, str]) -> None:
    _SCRIPTED_LOC.clear()
    _SCRIPTED_LOC.update(mapping)


# Any remaining bracket command, e.g. [This.GetName] — not statically resolvable.
_LOC_COMMAND    = re.compile(r'\[[^\]]*\]')

# Icons that are pure decoration / category markers — their name is never useful
# text (it only ever sits next to the thing it depicts), so drop them outright.
_ICON_DROP      = re.compile(r'^(job|mod|trigger|leader|relic|tradition|edict|ascension|pop_cat)_|^building$|_node$')

def _render_icon(m: re.Match) -> str:
    """£icon£ → its name as words, unless it's a decoration icon (dropped). Resource
    icons survive so a label like "£unity£ Produced per 100" keeps the resource. A
    `mod_planet_<stat>_<add|mult>` icon wraps a stat (e.g. mod_planet_stability_add)
    — render the stat so those labels keep it, rather than dropping the whole icon."""
    name = m.group(1)
    mp = re.match(r'^mod_planet_(.+?)_(?:add|mult)$', name)
    if mp:
        return _icon_words(mp.group(1))
    if _ICON_DROP.search(name):
        return ' '
    name = re.sub(r'^sr_', '', name)   # strategic-resource icon prefix (sr_living_metal
                                       # → "Living Metal", matching its $sr_…$ text twin)
    return _icon_words(name)

# Rendered icon words are wrapped in these private-use markers until _drop_icon_echo
# has seen them, so it can tell an icon's word from the same word written in the text.
_ICON_OPEN, _ICON_CLOSE = '\ue000', '\ue001'
_ICON_SPAN = re.compile(f'{_ICON_OPEN}([^{_ICON_CLOSE}]*){_ICON_CLOSE}')

def _icon_words(name: str) -> str:
    return f' {_ICON_OPEN}{name.replace("_", " ").title()}{_ICON_CLOSE} '

def _drop_icon_echo(s: str, reach: int = 3) -> str:
    """Drop an icon's word when the clause repeats it within `reach` words after it —
    "£pop£ §HOrganic Pops§!" → "Organic Pops", not "Pop Organic Pops" — and unwrap
    every other icon word. Stops at clause punctuation, so "£pop£ is enslaved" and
    "£pop£ gained: 3 pops" keep their icon word."""
    def sub(m: re.Match) -> str:
        name = [_wnorm(w) for w in m.group(1).split()]
        after = []
        for tok in _ICON_SPAN.sub(r'\1', s[m.end():]).split()[:reach + len(name)]:
            after.append(_wnorm(tok))
            if re.search(r'[.,;:!?)]$', tok):
                break
        k = len(name)
        echoed = any(after[i:i + k] == name for i in range(min(reach, len(after) - k) + 1))
        return '' if echoed else m.group(1)
    return _ICON_SPAN.sub(sub, s)

def _wnorm(w: str) -> str:
    """Word key for de-duplication: lowercased, punctuation-free, plural-folded."""
    w = re.sub(r'\W', '', w.lower())
    return w[:-1] if len(w) > 3 and w.endswith('s') else w

def _dedup_adjacent(s: str) -> str:
    """Collapse an icon name that just repeats the phrase beside it — single word
    ("Official Official Capacity" → "Official Capacity") or multi-word ("Consumer
    Goods Consumer Goods" → "Consumer Goods"). Keeps the *later* copy (the
    proper-cased / plural loc name). Icon names are ≤3 words, so that bounds the
    repeat length we look for."""
    toks = s.split()                       # any-whitespace split (drops icon padding)
    norm = [_wnorm(t) for t in toks]
    out: list[str] = []
    i, n = 0, len(toks)
    while i < n:
        dropped = False
        for k in range(min(3, (n - i) // 2), 0, -1):
            a, b = norm[i:i + k], norm[i + k:i + 2 * k]
            if all(a) and a == b:          # phrase repeats immediately → skip first copy
                i += k
                dropped = True
                break
        if not dropped:
            out.append(toks[i])
            i += 1
    return ' '.join(out)

def join_texts(texts) -> str | None:
    """Join several cleaned tooltip texts into one, one line each, dropping a line seen
    already — a second tooltip often restates the first and adds to it
    (`…_tt_2: "$…_tt$\\n…"`). None when nothing is left."""
    out: list[str] = []
    for t in texts:
        for line in (t or '').split('\n'):
            if line.strip() and line not in out:
                out.append(line)
    return '\n'.join(out) or None

# A $@scripted_variable$ ref, optionally with a Paradox number-format spec after '|'
# (e.g. $@cost|%0$). These only resolve when the caller passes the `variables` map.
_SCRIPTED_VAR_REF = re.compile(r'\$(@\w+)(?:\|([^$]*))?\$')

def _fmt_scripted(value: float, fmt: str | None) -> str:
    """Format a scripted-variable value per its loc format spec (the part after '|'):
    `%` = percentage (×100, trailing %); a digit sets decimal places; `+` forces a sign.
    E.g. -0.25 with `%0` → "-25%", 1.5 with `1` → "1.5"."""
    fmt = fmt or ''
    pct = '%' in fmt
    dm = re.search(r'\d', fmt)
    decimals = int(dm.group()) if dm else (0 if pct else 2)
    v = value * 100 if pct else value
    out = f'{v:.{decimals}f}'
    if '+' in fmt and value > 0:
        out = '+' + out
    return out + ('%' if pct else '')

def _clean_loc(s: str, loc: dict[str, str], keep_newlines: bool = False,
               variables: dict[str, float] | None = None) -> str:
    """Expand $KEY$ refs and ['concept', …] scripted-loc tags (up to 6 rounds),
    strip §X colour codes and [command] tags, and resolve £icon£ refs.

    Icons are pictures. Decoration/category icons (job/mod/trigger/building/…) are
    dropped; resource icons are rendered as their name so a label keeps its resource
    even when the loc doesn't repeat it ("£unity£ Produced per 100" → "Unity Produced
    per 100"). An icon name that simply repeats the word next to it is then collapsed
    ("£official£ Official Capacity" → "Official Capacity"). With keep_newlines=True
    the loc's \\n line breaks are preserved (tooltip rules stay on separate lines);
    otherwise everything collapses to one space-joined line (names, labels). When
    `variables` is given, $@scripted_var|fmt$ refs are resolved to formatted numbers."""
    # Concept tags and $KEY$ refs are interleaved each round: a $KEY$ can expand to text
    # that itself contains ['concept'] tags (and vice-versa), so resolve both until the
    # string stops changing — otherwise nested concepts survive only to be stripped as
    # [commands] below, leaving gaps (e.g. "Homeworld has a .").
    for _ in range(6):
        before = s
        s = _LOC_CONCEPT.sub(_concept_sub, s)   # ['concept'] / ['type:key'] tags
        s = _LOC_INLINE_REF.sub(lambda m: loc.get(m.group(1), m.group(0)), s)  # $KEY$
        s = _LOC_SCRIPTED.sub(  # [GetResearcherPluralWithIcon] → $job_researcher_plural…$
            lambda m: f'${_SCRIPTED_LOC[m.group(1)]}$' if m.group(1) in _SCRIPTED_LOC
            else m.group(0), s)
        if s == before:
            break
    if variables is not None:
        s = _SCRIPTED_VAR_REF.sub(
            lambda m: _fmt_scripted(variables[m.group(1)], m.group(2))
            if m.group(1) in variables else m.group(0), s)
    # [foundry.GetNamePlural] → the job's loc name ("Metallurgists") before the generic
    # [command] strip would drop it. Non-job scopes fall through and are stripped.
    s = _LOC_SCOPE_NAME.sub(
        lambda m: loc.get('job_' + m.group(1) + ('_plural' if m.group(2) else ''), ''), s)
    s = _LOC_COMMAND.sub('', s)        # drop leftover [Command.Chain] tags
    s = _LOC_FMT_CODE.sub('', s)       # strip §X colour codes
    s = _LOC_ICON.sub(_render_icon, s) # £icon£ → word (or dropped, if decoration)
    # Real newlines BEFORE de-dup so a \n actually separates tokens (otherwise
    # "Housing§!.\n§YGenerator" glues into one token and the repeated "Housing"
    # survives). Per line: remember its indentation ($t$ indents sub-items), then
    # de-dup its content.
    s = re.sub(r'\\n', '\n', s)        # literal \n → real line break (rule separator)
    s = re.sub(r'\\[tr]', ' ', s)      # \t \r → space
    rows: list[tuple[int, str]] = []
    for raw in s.split('\n'):
        content = _dedup_adjacent(_drop_icon_echo(raw))
        if content:
            # $t$ is four spaces: indent in those steps, so the space an icon leaves at the
            # start of a line ("\n£pop_cat_worker£ Workers") is no sub-item
            rows.append(((len(raw) - len(raw.lstrip())) // 4, content))
    if not keep_newlines:
        return re.sub(r'\s+', ' ', ' '.join(c for _, c in rows)).strip()
    # Tooltip: one rule per line, "- " bullet stripped. A more-indented line is a
    # sub-item of the line above (e.g. a tech under "Additional Starting
    # Technologies:") — fold it back up instead of leaving it dangling.
    out: list[str] = []
    out_indents: list[int] = []
    for indent, content in rows:
        body = re.sub(r'^[-•]\s*', '', content).strip()
        # More indented than the line it would attach to → it's a sub-item; fold it up.
        # Compare to that line's indent (not the previous row's) so several siblings at
        # the same sub-indent all join: "Terms:" + a + b + c. After a colon the first
        # joins with a space, the rest as a comma list.
        if out and indent > out_indents[-1]:
            out[-1] += (' ' if out[-1].rstrip().endswith(':') else ', ') + body
        else:
            out.append(body)
            out_indents.append(indent)
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Localisation parsing (for species class display names)
# ---------------------------------------------------------------------------

# Leading whitespace is optional: most files indent entries, but some DLC files
# (e.g. the Infernals Species Pack) put them at column 0. The `l_english:` header
# never matches (it has no "value"); `#` comments aren't word-initial.
_LOC_ENTRY = re.compile(r'^\s*(\w[\w.]*):(\d*)\s+"([^"]*)"', re.MULTILINE)


_LOC_REF = re.compile(r'^\$(\w+)\$$')


def load_localisation(loc_dir: Path) -> dict[str, str]:
    """
    Parse all .yml localisation files, returning {key: label}.
    When the same key appears multiple times, the entry with the lowest
    version number wins (e.g. :0 beats :1). Single-token $KEY$ references
    are resolved one level deep; unresolvable references are dropped (NULL).
    """
    best: dict[str, tuple[int, str]] = {}  # key → (version, label)
    for f in sorted(loc_dir.rglob('*.yml')):
        try:
            text = f.read_text(encoding='utf-8-sig', errors='replace')
        except Exception:
            continue
        for m in _LOC_ENTRY.finditer(text):
            key, ver_str, label = m.group(1), m.group(2), m.group(3)
            ver = int(ver_str) if ver_str else 0
            if key not in best or ver < best[key][0]:
                best[key] = (ver, label)
    labels = {k: lbl for k, (_, lbl) in best.items()}
    # Resolve single-token $KEY$ references one level deep
    for key, lbl in list(labels.items()):
        ref = _LOC_REF.match(lbl)
        if ref:
            resolved = labels.get(ref.group(1))
            if resolved and not _LOC_REF.match(resolved):
                labels[key] = resolved
            else:
                del labels[key]  # unresolvable — fall back to NULL in DB
    return labels


def inline_refs(text: str, loc: dict[str, str]) -> str:
    """`$key$` references inside a text replaced by their loc, one level deep (focus requirements
    "£trigger_yes£ $conquest.100.req$", leader traits "$trait_ruler_eye_for_talent$ II")."""
    return re.sub(r'\$([\w.]+)\$', lambda m: loc.get(m.group(1), m.group(0)), text)

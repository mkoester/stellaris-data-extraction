"""Active mods on top of the game: a merged copy of the install folder, as the game loads it.

The readers all take one install folder and glob it, so mods reach them as a folder of their own
(`overlay()`): the game's files, each mod's files over them in load order. Two rules of the game
are applied while building it:

- **A file replaces the file at the same path** (earlier mod or vanilla); `replace_path` in a mod's
  `descriptor.mod` drops the earlier files of that folder.
- **A key defined in two files of one `common/` folder: the later file wins** (files load by name,
  across all sources — hence the `zzz_` prefixes of the rebalance mods), **in `scripted_variables`
  the first** (hence the `!`/`!!` prefixes there). The losing definition is cut out of its file, so
  every reader — `parse_file` or raw text — sees one. Folders whose files merge rather than
  replace (`MERGED`) are left alone.

Localisation: a mod's English files outside `localisation/english/` (older layout,
`localisation/<x>_l_english.yml`) are moved under it; other languages are dropped. Keys under a
`replace/` folder win (`localisation.load_localisation`).
"""

from pathlib import Path
import os
import shutil

from .clausewitz import parse_file

# Top-level folders the readers use that mods can add to; every other entry is the game's own.
OVERLAID = ('common', 'events', 'localisation', 'localisation_synced', 'prescripted_countries',
            'interface', 'map')
# `common/` folders whose files merge (event lists, define values) or are not keyed definitions.
MERGED = {'on_actions', 'defines', 'inline_scripts'}
FIRST_WINS = {'scripted_variables'}
COLOR_TAGS = {'rgb', 'hsv', 'hsv360', 'hex'}
_DELIM = set(' \t\r\n={}<>!?#"')


def descriptor(mod_dir: Path) -> dict:
    """The mod's own `descriptor.mod` (name, version, replace_path), or {} without one."""
    f = mod_dir / 'descriptor.mod'
    try:
        return parse_file(f) if f.exists() else {}
    except Exception:
        return {}


def _list(v) -> list[str]:
    return [str(x).strip('"') for x in (v if isinstance(v, list) else [v] if v else [])]


def _localisation_target(rel: str) -> str | None:
    """Where a mod's localisation file goes in the merged view; None: not English."""
    parts = rel.split('/')
    if not parts[-1].endswith('_l_english.yml'):
        return None
    if len(parts) > 2 and parts[1] == 'english':
        return rel
    return '/'.join(['localisation', 'english', *parts[1:]])


def _files(root: Path, mod: bool) -> dict[str, Path]:
    """Relative path → file for the overlaid folders of one source."""
    out: dict[str, Path] = {}
    for top in OVERLAID:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, _, names in os.walk(base, followlinks=True):
            for name in names:
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                if mod and top == 'localisation':
                    rel = _localisation_target(rel)
                    if rel is None:
                        continue
                out[rel] = path
    return out


def merged_files(game_dir: Path, mods: list[Path]) -> tuple[dict[str, Path], set[str]]:
    """Relative path → source file after all mods, and the paths a mod supplied."""
    files = _files(game_dir, mod=False)
    from_mods: set[str] = set()
    for mod in mods:
        for folder in _list(descriptor(mod).get('replace_path')):
            folder = folder.strip('/')
            for rel in [r for r in files if r.rsplit('/', 1)[0] == folder]:
                del files[rel]
                from_mods.discard(rel)
        own = _files(mod, mod=True)
        files.update(own)
        from_mods |= set(own)
    return files, from_mods


# --- top-level blocks of a Clausewitz file, with their position in the text -------------------

def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        c = text[i]
        if c == '#':
            j = text.find('\n', i)
            i = n if j < 0 else j + 1
        elif c.isspace():
            i += 1
        else:
            break
    return i


def _token(text: str, i: int) -> tuple[str, int]:
    if text[i] == '"':
        j = text.find('"', i + 1)
        j = len(text) if j < 0 else j + 1
        return text[i + 1:j - 1], j
    j = i
    while j < len(text) and text[j] not in _DELIM:
        j += 1
    return text[i:j], max(j, i + 1)


def _skip_block(text: str, i: int) -> int:
    """From a `{` to just past its matching `}` (strings and comments respected)."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = text.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '#':
            j = text.find('\n', i)
            i = n if j < 0 else j + 1
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def top_level_spans(text: str) -> list[tuple[str, int, int]]:
    """Each top-level `key <op> value` as (key, start, end) in `text`."""
    out: list[tuple[str, int, int]] = []
    i, n = 0, len(text)
    while True:
        i = _skip_ws(text, i)
        if i >= n:
            return out
        if text[i] == '{':
            i = _skip_block(text, i)
            continue
        if text[i] in '}=<>!?':
            i += 1
            continue
        start = i
        key, i = _token(text, i)
        i = _skip_ws(text, i)
        if i >= n or text[i] not in '=<>!?':
            continue  # a bare word at the top level: no definition
        while i < n and text[i] in '=<>!?':
            i += 1
        i = _skip_ws(text, i)
        if i >= n:
            return out
        if text[i] == '{':
            i = _skip_block(text, i)
        else:
            value, i = _token(text, i)
            j = _skip_ws(text, i)
            if value.lower() in COLOR_TAGS and j < n and text[j] == '{':
                i = _skip_block(text, j)
        out.append((key, start, i))


def _keyed(folder: str, key: str) -> bool:
    return key.startswith('@') == (folder in FIRST_WINS)


def losing_spans(folder: str, texts: list[tuple[str, str]]) -> dict[str, list[tuple[str, int, int]]]:
    """Per file name, the definitions another file of the folder overrides. `texts` in load
    order (file name); a key defined twice in one file is a wrapper (`defined_text = {…}`), not
    an id, and is never cut."""
    spans = {name: top_level_spans(text) for name, text in texts}
    wrappers: set[str] = set()
    for found in spans.values():
        keys = [k for k, _, _ in found]
        wrappers |= {k for k in keys if keys.count(k) > 1}
    owner: dict[str, str] = {}
    for name, _ in texts:
        for key, _, _ in spans[name]:
            if key in wrappers or not _keyed(folder, key):
                continue
            if folder in FIRST_WINS:
                owner.setdefault(key, name)
            else:
                owner[key] = name
    return {name: [s for s in found if s[0] in owner and owner[s[0]] != name]
            for name, found in spans.items()
            if any(s[0] in owner and owner[s[0]] != name for s in found)}


def cut(text: str, spans: list[tuple[str, int, int]]) -> str:
    for _, start, end in sorted(spans, key=lambda s: s[1], reverse=True):
        text = text[:start] + text[end:]
    return text


# --- building the merged folder ----------------------------------------------------------------

def _link(src: Path, dst: Path) -> None:
    """A link where the platform allows one (Windows: no symlinks without developer mode), else a
    copy. Directories: symlink, Windows junction, copy."""
    try:
        os.symlink(src, dst, target_is_directory=src.is_dir())
        return
    except OSError:
        pass
    if src.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(src), str(dst))
            return
        except (ImportError, OSError):
            shutil.copytree(src, dst)
            return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def overlay(game_dir: Path, mods: list[Path], out: Path) -> dict[str, int]:
    """Write the merged view of `game_dir` plus `mods` (load order) to the empty folder `out`.
    Returns per mod folder how many definitions of earlier files it overrode."""
    files, from_mods = merged_files(game_dir, mods)
    for entry in game_dir.iterdir():
        if entry.name not in OVERLAID:
            _link(entry, out / entry.name)
    for rel, src in files.items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _link(src, dst)

    overrides: dict[str, int] = {str(m): 0 for m in mods}
    owner_of = {rel: next((str(m) for m in reversed(mods) if files[rel].is_relative_to(m)), None)
                for rel in from_mods}
    folders = {rel.rsplit('/', 1)[0] for rel in from_mods if rel.startswith('common/')}
    for folder in sorted(folders):
        name = folder.split('/')[1] if '/' in folder else ''
        if name in MERGED or not (out / folder).is_dir():
            continue
        names = sorted(p.name for p in (out / folder).iterdir() if p.suffix == '.txt')
        texts = [(n, (out / folder / n).read_text(encoding='utf-8-sig', errors='replace'))
                 for n in names]
        by_name = dict(texts)
        for fname, lost in losing_spans(name, texts).items():
            dst = out / folder / fname
            dst.unlink()
            dst.write_text(cut(by_name[fname], lost), encoding='utf-8')
            winners = {k for k, _, _ in lost}
            for other, text in texts:
                mod = owner_of.get(f'{folder}/{other}')
                if mod and other != fname:
                    overrides[mod] += len(winners & {k for k, _, _ in top_level_spans(text)})
    return overrides

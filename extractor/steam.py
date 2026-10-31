"""Which Steam branch the game install is on — a beta's files must never become the committed dump."""

from pathlib import Path
import re

APP_ID = 281990
REPO = Path(__file__).resolve().parent.parent
# The outputs a regular release ships: the dump the planner's deployment builds from, and its DB.
RELEASE_OUTPUTS = (REPO / 'sql' / 'stellaris.sql', REPO / 'sqlite' / 'stellaris.sqlite')


def manifest_path(install_dir: Path) -> Path:
    """Steam keeps `appmanifest_<id>.acf` in `steamapps/`, two levels above `common/Stellaris`."""
    return install_dir.resolve().parent.parent / f'appmanifest_{APP_ID}.acf'


def branch_from_manifest(text: str) -> str:
    """The branch whose files are installed: MountedConfig's BetaKey (UserConfig's is only the
    choice, before Steam has updated); no BetaKey means the public branch."""
    mounted = re.search(r'"MountedConfig"\s*\{([^}]*)\}', text)
    key = re.search(r'"BetaKey"\s+"([^"]*)"', mounted.group(1) if mounted else text)
    return key.group(1) if key and key.group(1) else 'public'


def steam_branch(install_dir: Path) -> str | None:
    """The installed branch, or None when there is no Steam manifest to tell."""
    path = manifest_path(install_dir)
    return branch_from_manifest(path.read_text(errors='replace')) if path.exists() else None


def release_output_refusal(targets, branch: str | None) -> str | None:
    """Why writing these targets is refused, or None. Only the repo's own release outputs are
    guarded, and only off the public branch (or when the branch is unknown): a player's dashboard
    extracts its beta install to a file of its own, and that must keep working."""
    hits = [t for t in targets if t and Path(t).resolve() in RELEASE_OUTPUTS]
    if not hits or branch == 'public':
        return None
    why = (f"the game is on Steam's beta branch '{branch}'" if branch
           else 'there is no Steam manifest to tell which branch the game is on')
    return (f"refusing to write {', '.join(map(str, hits))}: {why}. These are the release's "
            f"outputs and must hold the public game's data. Switch the game to the public branch "
            f"in Steam (Properties → Betas → None), or write elsewhere for a beta's data.")


if __name__ == '__main__':
    import sys
    # `python3 -m extractor.steam <install dir>`: the installed branch, exit 1 when not public.
    found = steam_branch(Path(sys.argv[1]).expanduser())
    print(found or 'unknown (no Steam manifest)')
    sys.exit(0 if found == 'public' else 1)

# Releasing

`main` carries releases, `develop` the next one; every release is a `--no-ff` merge into `main`,
tagged `vX.Y.Z` (the OKF vault's procedure, `practices/git-and-workspaces.md`). `develop` already
carries the version being released (`__version__` in `extractor/__init__.py`).

Two things depend on a release here: the planner's deployment builds its DB from `main`'s
`sql/stellaris.sql`, and the dashboard's zips bundle the extractor from a tag.

1. On `develop`, everything committed, `sql/stellaris.sql` regenerated if the extraction changed
   (`AGENTS.md`). In `CHANGELOG.md`, turn `Unreleased` into `## [X.Y.Z] - <date>` (own commit).
2. `git checkout main && git merge --no-ff develop -m "Release vX.Y.Z (merge develop)"`, then
   `git tag vX.Y.Z`.
3. `git checkout develop`, bump the patch of `__version__`, commit `chore: version bump (vX.Y.Z+1)`.
4. Publish (by hand): `git push origin main develop --tags`.

## Beta build (a game's open beta)

Work specifically for a running open beta lives on the branch **`open-beta`** (cut from `develop`,
`develop` merged in as needed). The dashboard's beta zips bundle the extractor from a tag, so:

1. On `open-beta`, everything committed: set `__version__` to `X.Y.Z-beta.N` (X.Y.Z the version
   `develop` carries, N counting from 1) and commit `chore: version X.Y.Z-beta.N` — the dashboard
   keys its extracted data on `__version__`, so a beta and the later release must differ.
2. `git tag vX.Y.Z-beta.N`, then publish (by hand): `git push origin open-beta vX.Y.Z-beta.N`.
3. When the game patch goes live: `git checkout develop && git merge --no-ff open-beta`, set
   `__version__` back to `X.Y.Z` (the merge brings the beta's), then the normal release above;
   delete `open-beta` (local and remote) once released. `sql/stellaris.sql` stays the released
   game's until then — regenerate it from the live patch, not the beta.

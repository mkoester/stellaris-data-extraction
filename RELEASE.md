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

# Contributing

Thanks for helping. Issues and pull requests are welcome; please keep changes small and focused.

## Branches

| Branch | Purpose |
|---|---|
| `main` | Released code only. Every commit on `main` is a tagged release (`vX.Y.Z`). |
| `develop` | Integration branch. Feature work is merged here first. Default target for PRs. |
| `feature/<short-name>` | New work, branched from `develop`, merged back into `develop` by PR. |
| `fix/<short-name>` | Non-urgent bug fixes, same flow as features. |
| `release/X.Y.Z` | Cut from `develop` to prepare a release (version bump, CHANGELOG); PR into `main`. |
| `hotfix/X.Y.Z` | Urgent fix for a released version, branched from `main`; PR into `main`, then merged back into `develop`. |

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`,
`refactor: …`, `test: …`, `chore: …`, `build: …`, `ci: …`. Breaking changes: `feat!: …` plus a
`BREAKING CHANGE:` footer. Keep the subject under ~72 characters and in the imperative.

## Versioning and releases

[Semantic Versioning](https://semver.org/): MAJOR for breaking changes (API, database or CSV formats),
MINOR for features, PATCH for fixes.

1. `git switch develop && git pull` then `git switch -c release/X.Y.Z`.
2. Bump `__version__` in `server/__init__.py` and `version` in `frontend/package.json`; move the
   CHANGELOG entries under `## X.Y.Z`.
3. Open a PR `release/X.Y.Z → main`; CI runs once. Merge with a merge commit.
4. Tag and publish: `git switch main && git pull && git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`
   then `gh release create vX.Y.Z --notes-file <the CHANGELOG section>`.
5. Merge `main` back into `develop`.

Releases are source-only: everyone builds the Docker image locally (`docker compose up -d --build`).
No prebuilt image is published, because the image bundles the Claude Code CLI, which is licensed by
Anthropic separately and is downloaded by each user at build time.

## CI

One workflow (`.github/workflows/ci.yml`) runs backend tests and the frontend typecheck/build on pull
requests to `main` and `develop` only. Please don't add scheduled, per-push or Docker-publishing jobs.

## Before opening a PR

```bash
python -m unittest discover -s tests
cd frontend && npm run typecheck && npm run build
```

Never commit personal data: no real holdings in tests or screenshots (use made-up numbers), no
`data/`, `.sqlite3`, `.env` or exported CSV files. `.gitignore` blocks the usual paths, but check
`git status` before committing.

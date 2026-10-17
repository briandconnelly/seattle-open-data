# Releasing

`.github/workflows/release.yml` builds, checks, and publishes a release. The wheel, sdist, and `.mcpb` bundle are built once by `.github/workflows/build.yml`, smoke-tested on Linux, macOS, and Windows (`scripts/smoke_test.py`), and the same files are published.

## Cut a release

1. Bump `version` in `pyproject.toml` and the README status line, run `make manifest` and `uv lock`, and move the CHANGELOG `[Unreleased]` entries under a new `## [X.Y.Z] - YYYY-MM-DD` heading. Merge.
2. Rehearse from `main`: `gh workflow run release.yml -f publish_to=testpypi`. It runs the version check, the full gate, the build, and the smoke tests, then uploads the wheel and sdist to TestPyPI. It never touches PyPI or GitHub Releases. TestPyPI accepts each version once, so a repeat rehearsal skips the upload; use `-f publish_to=none` to rehearse without it.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Approve the `pypi` deployment when GitHub asks. The workflow then uploads to PyPI and creates the GitHub Release with the wheel, sdist, and `.mcpb`, using the CHANGELOG section as its notes.

The workflow refuses to publish when the tag, `pyproject.toml`, `manifest.json`, and the latest CHANGELOG version disagree.

## One-time setup

- **PyPI trusted publisher.** On pypi.org, add a pending publisher for project `seattle-open-data`: owner `briandconnelly`, repository `seattle-open-data`, workflow `release.yml`, environment `pypi`.
- **TestPyPI trusted publisher.** The same on test.pypi.org, with environment `testpypi`.
- **GitHub environments.** In the repository settings, create `pypi` with a required reviewer (the maintainer), and `testpypi` with none.

No API tokens are stored anywhere: both indexes authenticate the workflow through OIDC.

# Release procedure

The repository builds distributions on every pull request. A `v*` tag is the only
publication trigger.

## One-time PyPI setup

Create the `northstar-runtime` project (or a pending trusted publisher) on PyPI and
configure a GitHub trusted publisher with:

- owner: `Jairogelpi`
- repository: `northstar`
- workflow: `release.yml`
- environment: `pypi`

Create a protected GitHub environment named `pypi`. No long-lived PyPI token belongs
in repository secrets; the workflow uses OIDC.

## Release checklist

1. Change the version in `pyproject.toml` and `src/northstar/__init__.py`.
2. Move the changelog section from `Unreleased` to the release date.
3. Run:

   ```bash
   python -m pytest --cov=northstar --cov-report=term-missing
   ruff check src tests
   python -m build --sdist --wheel
   python -m twine check dist/*
   northstar bench
   ```

4. Merge only after the full Ubuntu/Windows matrix and package smoke job pass.
5. Create an annotated tag matching the package version and push it:

   ```bash
   git tag -a v0.2.0 -m "Northstar v0.2.0"
   git push origin v0.2.0
   ```

The release workflow then builds once, clean-installs the wheel, generates a
CycloneDX SBOM, creates GitHub build-provenance attestations, publishes through PyPI
trusted publishing, and creates a GitHub release with wheel, sdist and SBOM assets.

Afterward, verify a fresh install from PyPI and compare the uploaded artifact hashes
with the GitHub release assets. Do not upload artifacts built on a developer machine.

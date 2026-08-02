# Releasing Marketmon

Marketmon uses semantic versions and annotated Git tags. A tag named `vX.Y.Z`
publishes the matching package version to PyPI. Tags are created only from an
up-to-date `main` branch after tests pass; release tags are never moved or reused.

## One-time PyPI setup

1. In the PyPI account publishing settings, add a pending GitHub trusted
   publisher for the project name `marketmon`. The first successful workflow
   run creates the project; a pending publisher does not reserve the name.
2. Use these publisher values:
   - Owner: `brightonsmith`
   - Repository: `marketplace-monitor`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In the GitHub repository, create an environment named `pypi`. Add required
   reviewers there if releases should require manual approval.

The release workflow uses OpenID Connect, so no long-lived PyPI token is stored
in GitHub. Do not push the first tag until the pending publisher is configured.

## Patch release example

1. Update `project.version` in `pyproject.toml` to `0.3.1` in the release PR.
2. Merge the PR after CI passes.
3. Update local `main`, verify the version, and create an annotated tag:

   ```bash
   git switch main
   git pull --ff-only origin main
   git tag -a v0.3.1 -m "Release 0.3.1"
   git push origin v0.3.1
   ```

4. Confirm the `release` workflow succeeds, then verify the published artifact:

   ```bash
   python -m pip install "marketmon==0.3.1"
   marketmon --version
   ```

Use patch versions for backward-compatible fixes, minor versions for new
backward-compatible features, and major versions for breaking changes.

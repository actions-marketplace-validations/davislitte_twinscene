# Releasing

## First launch (once)

1. Create the public GitHub repository `twinscene` and push `main` and the `v0.1.0` tag.
2. **Settings → Actions → General**: allow GitHub Actions to create and approve pull requests (the sync action
   needs it; see docs/github-action.md).
3. Check that the **tests** workflow passes on Linux and Windows (it has only been run locally on Windows so far).
4. Pushing the `v0.1.0` tag runs the **release** workflow: a GitHub release with the built package, and the `v0`
   tag that `uses: davislitte/twinscene@v0` points at.
5. **PyPI** (so `pip install twinscene` works; the name was free on 2026-10-08):
   - on pypi.org, add a *pending trusted publisher*: project `twinscene`, this repository, workflow `release.yml`,
     environment `pypi`;
   - in the repository, create the environment `pypi` and set the variable `PYPI_PUBLISH` to `true`;
   - re-run the release workflow (or push the next tag).
6. Try the GitHub Action on a test repository (the starter example works) before announcing.
7. Optional: submit the Godot plugin to the Godot Asset Library, and the Unity package to OpenUPM.

## Every release

1. Update `CHANGELOG.md`, and the version in `twinscene/__init__.py`, `addons/unity/package.json` and
   `addons/godot/addons/twinscene/plugin.cfg`.
2. Run the unit tests and the in-editor checks in `tests/tools/` (see CONTRIBUTING.md).
3. Commit, tag `vX.Y.Z`, push the tag. The release workflow does the rest.

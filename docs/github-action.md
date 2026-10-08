# Running TwinScene on GitHub

Put both projects in one repository:

```
unity/          the Unity project (Assets/, ProjectSettings/, Packages/)
godot/          the Godot project (project.godot)
twinscene.json  optional; see twinscene/project.py for the options
```

Then add `.github/workflows/twinscene.yml`:

```yaml
name: TwinScene sync
on:
  push:
    branches: [main]
    paths: ["unity/**", "godot/**", "twinscene.json"]
  workflow_dispatch:
    inputs:
      prefer:
        description: Settle conflicts in favour of
        type: choice
        options: ["", unity, godot]

permissions:
  contents: write
  pull-requests: write

concurrency:
  group: twinscene
  cancel-in-progress: true

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: davislitte/twinscene@v0
        with:
          prefer: ${{ inputs.prefer }}
```

The repository also needs **Settings → Actions → General → "Allow GitHub Actions to create and approve pull
requests"** turned on.

## What happens

1. Someone pushes Unity or Godot changes to `main`.
2. The action runs `twinscene sync` and, if anything changed, pushes the result to the `twinscene/sync` branch and
   opens a pull request ("TwinScene: sync from Unity", "... from Godot", or both). Its description lists every
   change and any conflicts.
3. A person reviews and merges it. That merge is a push to `main` too, but now both sides agree, so the next run finds
   nothing to do: no loops.

Pushes made while a sync pull request is open simply rebuild it from the new `main`.

## Conflicts

When the same thing was changed differently on both sides, the pull request lists it in a table and leaves both
sides as they are. Settle it by making the two sides agree, or run the workflow by hand (**Actions → TwinScene sync
→ Run workflow**) with `prefer` set to the side that should win.

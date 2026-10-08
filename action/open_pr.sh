#!/usr/bin/env bash
# Commits what `twinscene sync` changed to $BRANCH and opens (or updates) a pull request into $BASE.
# The sync is recomputed from $BASE on every run, so the branch is simply replaced each time.
set -euo pipefail

if [ -z "$(git status --porcelain)" ]; then
  echo "Nothing to sync."
  echo "changed=false" >> "$GITHUB_OUTPUT"
  exit 0
fi
echo "changed=true" >> "$GITHUB_OUTPUT"

to_godot=$(grep -c "^\*\*Unity → Godot\*\*" "$REPORT" || true)
to_unity=$(grep -c "^\*\*Godot → Unity\*\*" "$REPORT" || true)
if [ "$to_godot" -gt 0 ] && [ "$to_unity" -gt 0 ]; then title="TwinScene: sync Unity ↔ Godot"
elif [ "$to_unity" -gt 0 ]; then title="TwinScene: sync from Godot"
else title="TwinScene: sync from Unity"; fi
if [ "${CONFLICTS:-false}" = "true" ]; then title="$title (conflicts to settle)"; fi

body="$RUNNER_TEMP/twinscene-pr.md"
{
  echo "Changes made on one side, copied to the other by TwinScene."
  echo "Merge this to keep both projects in step. It is rebuilt whenever \`$BASE\` changes."
  echo
  cat "$REPORT"
} > "$body"

git config user.name "twinscene[bot]"
git config user.email "twinscene-bot@users.noreply.github.com"
git switch -C "$BRANCH"
git add -A
git commit --quiet -m "$title"
git push --force --quiet origin "$BRANCH"

if url=$(gh pr view "$BRANCH" --json url,state --jq 'select(.state == "OPEN") | .url' 2>/dev/null) && [ -n "$url" ]; then
  gh pr edit "$BRANCH" --title "$title" --body-file "$body" > /dev/null
else
  url=$(gh pr create --base "$BASE" --head "$BRANCH" --title "$title" --body-file "$body")
fi
echo "Sync pull request: $url"
echo "url=$url" >> "$GITHUB_OUTPUT"

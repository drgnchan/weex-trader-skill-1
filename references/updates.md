# Update Channel

## Author release flow

1. Bump `metadata.version` in `SKILL.md`.
2. Package skill:

```bash
python3 /Users/raymond/.codex/skills/fs-skill-creator/scripts/package_skill.py /path/to/weex-trader-skill ./dist
```

3. Create GitHub Release with tag like `v1.1.0`.
4. Upload `weex-trader-skill.skill` as release asset.

## User update flow

Check update:

```bash
python3 scripts/skill_update.py check --repo <owner>/<repo>
```

Apply update:

```bash
python3 scripts/skill_update.py update --repo <owner>/<repo>
```

Optional env for GitHub API quota/private repos:

```bash
export GITHUB_TOKEN="..."
```

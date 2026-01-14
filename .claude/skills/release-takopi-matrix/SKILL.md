---
name: release-takopi-matrix
description: Release takopi-matrix to PyPI via git tag
---

# /release-takopi-matrix - Release Workflow

Release a new version of takopi-matrix to PyPI.

## When to Use

- "Release version X.Y.Z"
- "Publish to PyPI"
- "Cut a release"

## Pre-flight Checks

Before releasing, verify:

```bash
# Run tests
uv run pytest

# Build package
uv build

# Verify version files match
grep "version" pyproject.toml
grep "__version__" src/takopi_matrix/__init__.py
```

## Workflow

### 1. Generate Changelog

Get commits since the last release tag:

```bash
# Find last version tag
git describe --tags --abbrev=0

# Get commits since last tag (or all commits if first release)
git log $(git describe --tags --abbrev=0 2>/dev/null || echo --all)..HEAD --oneline
```

Create/update `CHANGELOG.md` with the new version entry:

```markdown
# changelog

## vX.Y.Z (YYYY-MM-DD)

### changes
- Feature description

### fixes
- Bug fix description

### docs
- Documentation update
```

**Changelog Format:**
- Lowercase headers
- Date format: `YYYY-MM-DD`
- Categories: `changes`, `fixes`, `docs`, `breaking`
- Link to PRs/commits where helpful
- Newest versions at top

### 2. Version Bump

Update version in both files:
- `pyproject.toml`: `version = "X.Y.Z"`
- `src/takopi_matrix/__init__.py`: `__version__ = "X.Y.Z"`

### 3. Create Commit & Tag

```bash
git add CHANGELOG.md pyproject.toml src/takopi_matrix/__init__.py
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

### 4. Verify

- Check GitHub Actions: https://github.com/Zorro909/takopi-matrix/actions
- Check PyPI: https://pypi.org/project/takopi-matrix/

## Example

```
User: /release-takopi-matrix 0.2.0

Claude: Preparing release v0.2.0...

Pre-flight checks:
✅ Tests: 193/193 passing
✅ Build: wheel + sdist created

📋 Commits since v0.1.0:
- abc1234 Add room-specific engine routing
- def5678 Fix Pydantic v2 config handling
- ghi9012 Update README and add CI workflows

📝 Generating changelog entry:

## v0.2.0 (2026-01-15)

### changes
- Add room-specific engine routing and project binding
- Add GitHub workflows for CI and PyPI publishing

### fixes
- Fix Pydantic v2 transports config handling in onboarding

### docs
- Update README with current features and configuration

Updating version files:
- pyproject.toml: 0.1.0 → 0.2.0
- __init__.py: 0.1.0 → 0.2.0
- CHANGELOG.md: Added v0.2.0 entry

Ready to commit and tag?
```

## Changelog Categories

| Category | When to Use |
|----------|-------------|
| `changes` | New features, enhancements, API changes |
| `fixes` | Bug fixes, error corrections |
| `docs` | Documentation updates, README changes |
| `breaking` | Breaking API changes (requires MAJOR bump) |

## Semantic Versioning

- **MAJOR** (1.0.0): Breaking API changes
- **MINOR** (0.2.0): New features, backwards compatible
- **PATCH** (0.1.1): Bug fixes, backwards compatible

## Rollback

If something goes wrong:

```bash
# Delete remote tag
git push origin --delete vX.Y.Z

# Delete local tag
git tag -d vX.Y.Z

# Revert commit
git revert HEAD
```

PyPI packages cannot be re-uploaded with the same version. Bump to a new patch version if needed.

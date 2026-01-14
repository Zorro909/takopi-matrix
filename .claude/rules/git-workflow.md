# Git Workflow

Always use feature branches for new commits. Never push directly to main/master.

## DO
- Create a feature branch before committing: `git checkout -b feature/description`
- Push to the feature branch: `git push origin feature/description`
- Create a PR from the feature branch

## DON'T
- Push commits directly to main/master
- Force push to main/master
- Assume you can push to the default branch

## Source
- 2026-01-14: Incorrectly pushed directly to main, then tried to force push to reset

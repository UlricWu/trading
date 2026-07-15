📝 Summary


# 🧩 Type of Change

Select one that best describes this PR. Release-relevant commits must use the
corresponding Conventional Commit type.

- [ ] feat: New feature
- [ ] fix: Bug fix
- [ ] perf: Performance improvement
- [ ] refactor: Code refactor (no behavior change)
- [ ] docs: Documentation only
- [ ] test: Tests only
- [ ] chore: Build/CI/Tools
- [ ] BREAKING CHANGE: Breaking API change

## 🔖 Release Rule Reminder

- `feat:` -> `minor + 1`
- `fix:` -> `patch + 1`
- commit message body 含 `BREAKING CHANGE:` -> `major + 1`
- `docs:`、`refactor:`、`chore:` 默认不触发正式版本发布
- `type!:` 不作为 breaking release 的唯一依据；如有不兼容变更，必须在 commit message body 写明 `BREAKING CHANGE:`

## Release PR Merge Strategy

Release PRs from `release/auto-release` to `master` must preserve the commits
from `dev`. Do not squash a release PR into the fixed `chore(release):` title;
use a merge commit or rebase merge so semantic-release can analyze the original
commit types.

## 📌 Related Issues
Link issues here (optional):

- Closes #
- Related #

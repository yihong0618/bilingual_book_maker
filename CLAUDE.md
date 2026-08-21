# bilingual_book_maker (pc418 fork)

This is a soft fork maintained as its own project. Upstream
(yihong0618/bilingual_book_maker) is no longer the source of truth — its CLI
format and much of its behavior are considered outdated here, and PRs sent
upstream are not being accepted.

## Fork policy

- **`main` on `origin` (pc418) is the trunk.** It carries our history and is
  not a mirror of upstream. Land work via feature branches merged `--no-ff`
  into main (see global branch rules).
- **Never rebase `main` onto upstream.** Merge or cherry-pick only — main's
  history is published and other branches hang off it.
- **Upstream intake is selective**, via the `upstream` remote:
  - Periodic full merge while codebases still overlap:
    `git fetch upstream && git merge upstream/main` (on a branch, then land).
  - Cherry-pick individual PRs as divergence grows (preferred long-term):
    `git fetch upstream pull/<N>/head:upstream-pr-<N>`, then cherry-pick or
    merge just that branch.
- **Tag each sync point** (`upstream-sync/YYYY-MM` at the upstream tip that
  was merged) so future syncs diff `upstream/main` against the last sync, not
  against our whole history.

## Repo rules

See `AGENTS.md`: `docs/*.md` and `plan.md` are local work records — never
commit them unless explicitly asked.

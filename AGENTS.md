# AGENTS.md

## Development Process

`PLANS.md` is the authoritative product plan, architecture plan, phase plan, stage plan, acceptance checklist, and technical decision record for this repository. Codex must follow `PLANS.md` throughout the long-running build.

`ART_PLAN.md` is the authoritative art-direction plan for this repository. When `ART_PLAN.md` conflicts with older visual-asset constraints in `PLANS.md`, follow `ART_PLAN.md` for art direction and asset format decisions while preserving the local-only and original-art requirements.

One worker process may perform at most one stage of a given phase. After completing a stage, that worker must stop instead of continuing into another stage of the same phase.

This project must use test-driven development. Codex writes or updates tests before implementing behavior, then implements until those tests pass.

The project must maintain a comprehensive testing suite, including:

- Unit tests
- Integration tests
- End-to-end tests
- Smoke tests
- Regression tests

Run the review processes defined in `PLANS.md` at the end of every phase:

- Rules review
- Backend review
- Frontend review
- AI review
- Final product review

## Git Workflow

Use `git add`, `git commit`, and `git push` throughout development. Commits must represent coherent, working increments toward the finished product.

One repository bootstrap commit seeds `main` with `AGENTS.md` and `PLANS.md`. This bootstrap commit is not a numbered phase. After that bootstrap commit, all numbered phases use feature branches and pull requests.

Each phase must be developed on the dedicated feature branch listed in the `PLANS.md` phase branch map, created from `main`.

The feature-branch, pull request, merge, and no-direct-main rules in this file must remain aligned with `PLANS.md`.

Start each phase with:

```powershell
git checkout main
git pull --ff-only origin main
git checkout -b <phase-branch-from-PLANS.md>
```

During each phase, commit and push after every completed stage and after every bug-fix cluster:

```powershell
git add .
git commit -m "phase N stage M: imperative summary"
git push -u origin <phase-branch-from-PLANS.md>
```

After the first push for a branch, use:

```powershell
git push
```

At the end of each phase, Codex must open a GitHub pull request into `main`, complete the required review process, merge the pull request into `main`, update local `main`, and start the next feature branch from the updated `main`.

Use GitHub CLI for pull requests and merges:

```powershell
gh pr create --base main --head <phase-branch-from-PLANS.md> --title "Phase N: <phase title from PLANS.md>" --body "Summary, tests, and review notes"
gh pr merge --squash --delete-branch
git checkout main
git pull --ff-only origin main
```

No new phase starts from an unmerged phase branch.

No direct commits to `main` occur after the repository bootstrap commit.

## Autonomy And Permissions

Codex has full, carte blanche authority to download, install, configure, and use any libraries, packages, Docker images, tools, or other dependencies needed to complete the project.

Codex must not ask for permission or clarification throughout the long-running process. In all cases, Codex must make its best informed judgment, figure it out, and continue working toward the end goal defined in `PLANS.md`: a fully working, live, playable local 3-tier Monopoly-style AI research game.

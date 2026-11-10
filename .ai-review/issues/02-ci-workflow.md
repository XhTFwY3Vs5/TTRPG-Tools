## Summary
The repo has a unittest suite but no CI, so regressions such as the two fresh-checkout failures fixed in #1 go unnoticed. Add a minimal GitHub Actions workflow that runs the suite on every push and pull request.

## Context
`AGENTS.md` (Running the System) gives the test command: `python3 -m unittest discover -s tests`. Dependencies are in `requirements.txt` (`mcp<2`, `flask`, `python-dotenv`, `replicate`, `openai`). There is no `.github/` directory. The README says Python 3.10+.

Relevant code (pinned to 2965eb7):
- [`requirements.txt`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/requirements.txt): runtime deps; nothing test-only is needed.
- [`README.md#L127-L136`](https://github.com/XhTFwY3Vs5/TTRPG-Tools/blob/2965eb7e513f77f793112528619717e9d7d3a4fa/README.md#L127-L136): the Tests section.

## Evidence
`ls .github` gives "No such file or directory". The repo has no PRs and no status checks.

## Proposed design
Create `.github/workflows/tests.yml`:
- `on: [push, pull_request]`
- one job, `runs-on: ubuntu-latest`, with a matrix `python-version: ["3.10", "3.13"]` (the documented floor plus the version the suite was checked on)
- steps: `actions/checkout@v4`, `actions/setup-python@v5` (with `cache: pip`), `pip install -r requirements.txt`, `python -m unittest discover -s tests -v`
- `permissions: contents: read`

No linting job yet. The repo has no linter config, and adding one is a separate decision.

## Implementation steps
1. Add `.github/workflows/tests.yml` as above.
2. Add a CI badge or a one-line note to the README's Tests section saying CI runs this command.

## Acceptance criteria
- [ ] Pushing the branch triggers the workflow, and it passes on both matrix entries.
- [ ] The workflow fails if a test fails. Verify once with a throwaway commit, then revert it.
- [ ] `python3 -m unittest discover -s tests` passes locally.

## Test plan
No new tests. The workflow run is the test.

## Out of scope
- No deploy, release, lint or coverage steps.
- Do not modify tests or production code.

## Risks and gotchas
- Pushing a file under `.github/workflows/` needs a token with the `workflow` scope. If the push is rejected, report it to a human rather than working around it.
- Python 3.10.0–3.10.11 lacks `tarfile` `filter=` (see the tar import issue). CI's 3.10 is a recent patch release, so it will not reproduce that.

## Dependencies
Blocked by: #1

## Metadata
Type: tests | Severity: medium | Confidence: verified | Effort: S | Reviewed at: 2965eb7e513f77f793112528619717e9d7d3a4fa on 2026-11-10

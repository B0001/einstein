# einstein-4 — GitHub fetcher

Status: **closed** (`bd close einstein-4`, evidence in the close reason and below).

## What I changed

- **`einstein/github_fetcher.py`** (new): `fetch_repos(query, *, max_results=30, http=None)`
  - Calls `GET https://api.github.com/search/repositories?q=...&sort=stars&order=desc&per_page=...`
  - Reads `GITHUB_TOKEN` from env; if set, sends `Authorization: Bearer <token>`. If unset, sends the request unauthenticated (lower rate limit, no auth header).
  - Maps each `items[]` entry to a `Record`: `type="repo"`, `id=full_name`, `title=full_name`, `summary` = `description` + space-joined `topics` (skips whichever part is falsy), `url=html_url`, `ts=pushed_at` (falls back to `updated_at`/`created_at`), `raw=item` (full unmodified payload).
  - **Non-200 → raises `GitHubFetchError(status_code, message, rate_limited=bool)`**, logged at `error` (or `warning` if rate-limited). This is the acceptance criterion: a non-200 is a raised, typed exception, never a silently-returned `[]`.
  - **Zero-results (200, empty `items`) → returns `[]`**, logged at `info`. This is the other half of the acceptance criterion — genuinely distinct code path and distinct outcome type (exception vs. empty list) from the non-200 case.
  - Rate-limit detection: `429` always flags `rate_limited=True`; `403` flags it only when `X-RateLimit-Remaining: 0` is present (a bare 403 — e.g. bad scopes — is not a rate limit and is reported as a plain non-200 error).
  - `http` param defaults to the `requests` module; tests inject a fake object with a `.get(url, **kwargs)` method — no `unittest.mock.patch` on `requests` itself, no network in the test process.

- **`tests/test_github_fetcher.py`** (new): 24 tests, no network. Command that proves it:
  ```
  uv run python -m unittest tests.test_github_fetcher -v
  ```
  Covers: success mapping (id/title/url/summary/raw), summary built from description+topics, missing-description case, zero-results returns `[]` without raising, `max_results` truncation, query/params sent correctly to the HTTP client, `GITHUB_TOKEN` present/absent controls the `Authorization` header, and every non-200 path (500, 401, 403-rate-limited, 403-not-rate-limited, 429, 503) raises `GitHubFetchError` with the right `status_code`/`rate_limited`, explicitly asserting the non-200 and zero-results paths are distinguishable by exception type.

## Full suite result (verbatim)

```
$ uv run python -m unittest discover tests
.................
----------------------------------------------------------------------
Ran 36 tests in 0.008s

OK
```
(12 pre-existing schema/embedding tests + 24 new github_fetcher tests = 36, all passing.)

## Live-API check (by hand, not part of `discover tests`, no `GITHUB_TOKEN` set in this container)

```
$ uv run python -c "
from einstein.github_fetcher import fetch_repos
for r in fetch_repos('quantum error correction', max_results=3):
    print(r.type, r.id, r.ts, r.summary[:80])
"
repo tqec/tqec 2026-08-10T01:13:58Z Design automation software tools for Topological Quantum Error Correction quantu
repo qiskit-community/qiskit-ignis 2022-06-30T18:28:46Z Ignis (deprecated) provides tools for quantum hardware verification, noise chara
repo qiskit-community/qiskit-qec 2025-01-27T10:23:37Z Qiskit quantum error correction framework framework quantum-error-correction sur
```
Real repos, real timestamps, `Record.__post_init__`'s `datetime.fromisoformat` accepted GitHub's `...Z` suffix natively (Python 3.12) with no manual string surgery needed.

```
$ uv run python -c "
from einstein.github_fetcher import fetch_repos
print(fetch_repos('completely_impossible_query_that_matches_absolutely_nothing_zzz9999xyz'))
"
[]

$ uv run python -c "
from einstein.github_fetcher import fetch_repos, GitHubFetchError
try:
    fetch_repos('repo:')   # malformed GitHub search syntax
except GitHubFetchError as e:
    print(e.status_code, e.rate_limited, str(e))
"
422 False GitHub search failed: 422 Unprocessable Entity for query='repo:'
```
This is the acceptance criterion demonstrated against the real API, not just fakes: a query that legitimately matches nothing returns `[]`; a query the API rejects outright raises a typed, distinct exception. I did not deliberately trigger a live 403/429 (would require burning the container's real unauthenticated rate limit, which is shared with whatever else in this environment might call GitHub) — that path's correctness rests on the unit tests, which construct the exact 429 / 403-with-zero-remaining response shapes GitHub documents and assert `rate_limited=True` for both.

## What I decided not to do, and why

- **No caching/backoff/retry loop.** CLAUDE.md says "back off and cache; do not retry in a tight loop" as a rule for *live workers*, but this bead's scope is the fetcher function itself — one HTTP call, mapped to Records, correct error surfacing. A caching layer would need to know about call sites (how often is this invoked, by what orchestrator, with what dedup key) that don't exist yet in this repo. Retrying belongs one layer up, in whatever calls `fetch_repos` repeatedly (not written yet — no bead for it exists either). Filing this as a new concern would be premature until there's a caller.
- **No pagination beyond one page.** `max_results` caps at `per_page` (GitHub's own max is 100); I did not implement multi-page fetching via `page=` because the bead's acceptance criterion doesn't call for it and nothing downstream (einstein-9, einstein-11) currently needs more than one page's worth of candidates. Easy to add later behind the same signature if a caller needs it.
- **Left `summary` un-normalized** (no lowercasing/whitespace collapsing) — matches how `Record.summary` is documented (raw-ish human-readable text), and `TfidfEmbedder` already lowercases/tokenizes downstream, so normalizing here would be redundant work with no observable effect.

## What I could not verify

- **Real 403 (rate-limited) and 429 responses from the live API.** I did not intentionally exhaust the container's real GitHub rate limit to observe these, since doing so would also block any other GitHub calls happening in this environment during this session. The code paths for both are covered by unit tests against response objects built to match GitHub's documented shapes (`403` + `X-RateLimit-Remaining: 0` header, and `429`), but that is "matches the docs," not "observed against the real API." Marking this unverified rather than claiming it as tested against live GitHub.
- **Behavior with a real `GITHUB_TOKEN`.** No token was available in this container, so the `Authorization: Bearer <token>` header is verified only by unit test (header gets set correctly) and by the unauthenticated live call succeeding — I did not confirm a token actually raises the live rate limit or is accepted by GitHub, since I have no token to test with.

## Commands to reproduce everything above

```bash
uv sync
uv run python -m unittest discover tests
uv run python -m unittest tests.test_github_fetcher -v
uv run python -c "from einstein.github_fetcher import fetch_repos; print(fetch_repos('quantum error correction', max_results=3))"
```

## Git status (not committed — conservative profile, per CLAUDE.md; awaiting explicit instruction)

```
$ git status --short
 M .beads/interactions.jsonl
 M .gitignore
?? .beads/issues.jsonl
?? .claude/settings.local.json
?? .python-version
?? README.md
?? einstein/          <- includes new github_fetcher.py
?? main.py
?? pyproject.toml
?? sandbox-prompt.md
?? tests/             <- includes new test_github_fetcher.py
?? uv.lock
```
Note: essentially the whole tree is untracked in this container (prior bead work — schema.py, embedding.py, pyproject.toml, etc. — is also untracked), not something introduced by this session. Suggested commands for a human to run, not executed by me:
```bash
git add einstein/github_fetcher.py tests/test_github_fetcher.py
git commit -m "einstein-4: GitHub repo search fetcher"
```
(A full `git add -A` / repo-init commit is a separate decision for whoever owns the untracked-tree state as a whole — out of scope for this bead.)

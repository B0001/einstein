"""Sandboxed execution + self-heal loop (einstein-19): run einstein-18's
codegen output against a real interpreter instead of trusting the LLM's say-
so, and feed a failure's stderr back to the model for one more try.

Per this repo's standard, `einstein.codegen.GeneratedCode.note` never says
more than "candidate reference implementation, not run or validated". This
module is what actually runs it. It does NOT upgrade the result to "correct"
or "novel" -- a successful run means "this snippet executed without raising,
against this pinned library version, this one time"; `SandboxOutcome.note`
is written to say exactly that, not more.

Isolation model (subprocess, not a full container -- the bead's own wording
allows either): each attempt runs in a fresh `tempfile.TemporaryDirectory` as
cwd, in a fresh `uv run --no-project --isolated --with <target_library>
--with <aer_version>` ephemeral environment (so a generated snippet's
`import qiskit` resolves against the exact pinned version from the codegen
prompt, without this repo ever adding qiskit/qiskit-aer to its own
`pyproject.toml` -- same reasoning `einstein.codegen`'s docstring gives),
with a stripped environment (`_sandbox_env`: PATH/HOME/UV_CACHE_DIR only --
none of `GITHUB_TOKEN`/`USPTO_API_KEY`/`OPENALEX_MAILTO` reach code an LLM
wrote and nothing here has audited), a wall-clock timeout, and
`start_new_session=True` so a timeout kills the whole process group
(`uv run` plus the `python` it spawns), not just `uv`'s own pid.

What this does NOT do: enforce a CPU or memory rlimit, or block outbound
network access from *inside* the generated code (only the timeout catches an
infinite loop or a hang; a snippet that opens a socket and behaves is not
stopped from doing so). `uv run --with` itself needs network to resolve
`target_library`/`aer_version` -- that is this module's own package-fetch,
not the generated code's, and it is why `_default_run` is never exercised by
`tests/` (see "the agent nodes need an LLM, and the tests must not" --
this module's version of that rule is "and the sandboxed run must not touch
the network either"; `tests/test_sandbox.py` injects a fake `run`). A real,
network-touching, real-qiskit-aer run is `scripts/sandbox_live_check.py`,
run by hand, same pattern as `scripts/harvest_gap_benchmark.py`.

`self_heal`'s retry loop: attempt 0 is `generated.code` as-is. On failure,
the failing code and its stderr go into a repair prompt (`_build_repair_
prompt`) and back to the LLM; the response is parsed with the same one-
fenced-` ```python ` -block convention `einstein.codegen._extract_code`
uses (parse failure here is `RepairParseError`, this module's own exception
-- not reused from `einstein.codegen.CodegenParseError`, following this
repo's existing pattern of one exception type per parse boundary:
`IdeationParseError`, `CodegenParseError`, now this). This repeats up to
`max_retries` times (`DEFAULT_MAX_RETRIES` is a knob, like
`novelty_auditor.DEFAULT_THETA` -- not a proven constant). Verdicts:
"validated" (attempt 0 succeeded, nothing to repair), "repaired" (a later
attempt succeeded), "failed" (every attempt failed, or a repair response
could not be parsed) -- "failed" always carries the last attempt's stderr in
`SandboxOutcome.traceback`, per the bead's acceptance criterion.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from einstein.codegen import DEFAULT_TARGET_LIBRARY, GeneratedCode
from einstein.ideator import LLMClient

# Same fence convention `einstein.codegen._CODE_FENCE_RE` parses -- kept as this
# module's own copy rather than importing that private name, so a repair
# response's parsing doesn't depend on codegen.py's internals.
_CODE_FENCE_RE = re.compile(r"```(?:python)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Pinned the same way DEFAULT_TARGET_LIBRARY is pinned in codegen.py (2026-09-24:
# `curl -s https://pypi.org/pypi/qiskit-aer/json` -> "0.17.2"). qiskit-aer is the
# execution backend a generated `QuantumCircuit` needs to actually run against --
# the codegen prompt (einstein-18) doesn't ask the model to import it, so the
# sandbox provides it as part of the run environment rather than depending on
# the generated code getting that import right. Not added to pyproject.toml, same
# reasoning as DEFAULT_TARGET_LIBRARY.
DEFAULT_AER_VERSION = "qiskit-aer==0.17.2"

TIMEOUT_SECONDS = 60.0

# Total attempts = 1 initial + this many repairs. Arbitrary knob (like
# novelty_auditor.DEFAULT_THETA) -- override with self_heal(..., max_retries=N).
DEFAULT_MAX_RETRIES = 2


class RepairParseError(Exception):
    """A repair response had no fenced ```python code block to extract.

    Distinct from `einstein.codegen.CodegenParseError` per this repo's
    one-exception-per-parse-boundary pattern (see module docstring). Carries
    `raw_response` for the same reason that one does: a caller can log or
    inspect exactly what the model said instead of a bare failure.
    """

    def __init__(self, message: str, *, gap_key: str, raw_response: str) -> None:
        self.gap_key = gap_key
        self.raw_response = raw_response
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of executing one code string once."""

    code: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.returncode == 0


RunFn = Callable[[str], RunResult]


@dataclass(frozen=True, slots=True)
class Attempt:
    """One iteration of the self-heal loop: the code tried and what running it did."""

    code: str
    result: RunResult
    is_repair: bool


@dataclass(frozen=True, slots=True)
class SandboxOutcome:
    """The end state of running one `GeneratedCode` through the self-heal loop.

    `verdict` is one of "validated" (ran clean first try), "repaired" (ran
    clean after at least one repair), or "failed" (never ran clean).
    `traceback` is the last attempt's stderr when `verdict == "failed"`
    (the bead's acceptance criterion: "fails after N with the traceback
    recorded"); empty string otherwise -- there is no failure to report.
    """

    gap_key: str
    subject_id: str
    verdict: str
    attempts: tuple[Attempt, ...]
    final_code: str
    traceback: str
    note: str


def _sandbox_env() -> dict[str, str]:
    """Minimal env for the sandboxed subprocess.

    Deliberately excludes `GITHUB_TOKEN`/`USPTO_API_KEY`/`OPENALEX_MAILTO`
    (the exact set `pyproject.toml`'s `[tool.sandbox.forward-env]` names) --
    those are this repo's own fetcher credentials, not something code an LLM
    wrote and nothing here has audited should ever see.
    """
    keep = ("PATH", "HOME", "UV_CACHE_DIR")
    return {k: v for k, v in os.environ.items() if k in keep}


def _default_run(
    code: str,
    *,
    target_library: str = DEFAULT_TARGET_LIBRARY,
    aer_version: str = DEFAULT_AER_VERSION,
    timeout: float = TIMEOUT_SECONDS,
) -> RunResult:
    """Execute `code` in a fresh `uv run --with` ephemeral environment.

    Not used by `tests/` -- see module docstring. Exercised by hand in
    `scripts/sandbox_live_check.py`.
    """
    with tempfile.TemporaryDirectory(prefix="einstein-sandbox-") as tmpdir:
        script_path = Path(tmpdir) / "candidate.py"
        script_path.write_text(code)
        cmd = [
            "uv", "run", "--no-project", "--isolated",
            "--with", target_library,
            "--with", aer_version,
            "python", str(script_path),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_sandbox_env(),
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return RunResult(
                code=code, returncode=proc.returncode, stdout=stdout, stderr=stderr, timed_out=False
            )
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return RunResult(
                code=code,
                returncode=-9,
                stdout=stdout,
                stderr=stderr + f"\n[sandbox] killed after exceeding {timeout}s timeout",
                timed_out=True,
            )


def _extract_repaired_code(raw_response: str, *, gap_key: str) -> str:
    match = _CODE_FENCE_RE.search(raw_response)
    if match is None:
        raise RepairParseError(
            f"could not find a fenced ```python code block in repair response for gap {gap_key!r}",
            gap_key=gap_key,
            raw_response=raw_response,
        )
    return match.group(1).strip("\n")


def _build_repair_prompt(generated: GeneratedCode, failing_code: str, stderr: str) -> str:
    lines = [
        "You previously wrote a Python module targeting the exact pinned library "
        f"version {generated.target_library} to implement: {generated.idea_method}",
        f"Problem/domain: {generated.idea_problem}",
        "",
        "That code failed when actually run. Here is the exact code that failed:",
        "```python",
        failing_code,
        "```",
        "",
        "Here is the traceback / stderr from running it:",
        "```",
        stderr.strip() or "(no stderr captured)",
        "```",
        "",
        "Fix the code so it runs successfully against the pinned library version above. "
        "Respond with exactly one fenced code block, language 'python', and no other text:",
        "```python",
        "<code>",
        "```",
    ]
    return "\n".join(lines)


def self_heal(
    generated: GeneratedCode,
    *,
    llm: LLMClient,
    run: RunFn = _default_run,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> SandboxOutcome:
    """Run `generated.code`; on failure, ask `llm` to repair it and retry.

    Up to `max_retries` repairs are attempted (total attempts <=
    `max_retries + 1`). Stops early -- before `max_retries` is exhausted --
    if a repair response can't be parsed (`RepairParseError`, caught here,
    not raised to the caller): a response with no code to try is not worth
    spending another attempt on, and the failure is recorded exactly like
    running out of retries. See module docstring for verdict semantics.
    """
    attempts: list[Attempt] = []
    code = generated.code

    for attempt_number in range(max_retries + 1):
        result = run(code)
        attempts.append(Attempt(code=code, result=result, is_repair=attempt_number > 0))

        if result.succeeded:
            verdict = "validated" if attempt_number == 0 else "repaired"
            note = (
                "ran clean on the first attempt, no repair needed"
                if attempt_number == 0
                else f"ran clean after {attempt_number} repair attempt(s)"
            )
            return SandboxOutcome(
                gap_key=generated.gap_key,
                subject_id=generated.subject_id,
                verdict=verdict,
                attempts=tuple(attempts),
                final_code=code,
                traceback="",
                note=note,
            )

        if attempt_number == max_retries:
            break

        raw_response = llm.generate(_build_repair_prompt(generated, code, result.stderr))
        try:
            code = _extract_repaired_code(raw_response, gap_key=generated.gap_key)
        except RepairParseError:
            break

    last = attempts[-1]
    repair_attempts = len(attempts) - 1
    note = (
        f"failed after {len(attempts)} attempt(s) ({repair_attempts} repair "
        f"{'retry' if repair_attempts == 1 else 'retries'} used of {max_retries} allowed) "
        "-- see traceback, not run successfully"
    )
    return SandboxOutcome(
        gap_key=generated.gap_key,
        subject_id=generated.subject_id,
        verdict="failed",
        attempts=tuple(attempts),
        final_code=last.code,
        traceback=last.result.stderr,
        note=note,
    )


def build_sandbox_agent_node(
    *,
    llm: LLMClient,
    run: RunFn = _default_run,
    max_retries: int = DEFAULT_MAX_RETRIES,
):
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    self-heals every `GeneratedCode` in `state["generated_code"]` and
    returns `{"sandbox_outcomes": ..., "agent_notes": ...}`. Not wired as
    `build_graph`'s default -- same reasoning as the ideator/auditor/codegen
    nodes: chaining the full loop into one graph is an integration decision
    for a later bead (einstein-20 is next, not this one).
    """

    def _node(state) -> dict:
        generated_code = state["generated_code"]
        outcomes = [self_heal(g, llm=llm, run=run, max_retries=max_retries) for g in generated_code]
        validated = sum(1 for o in outcomes if o.verdict == "validated")
        repaired = sum(1 for o in outcomes if o.verdict == "repaired")
        failed = sum(1 for o in outcomes if o.verdict == "failed")
        summary = (
            f"sandbox: {len(outcomes)} candidate(s) run -- {validated} validated, "
            f"{repaired} repaired, {failed} failed"
        )
        return {"sandbox_outcomes": outcomes, "agent_notes": [summary]}

    return _node


class _StubLLM:
    """Deterministic stand-in used by this module's own self-check."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def generate(self, prompt: str) -> str:
        return self._responses.pop(0)


def _self_check() -> None:
    generated = GeneratedCode(
        gap_key="true_invention_gap:paper:2508.00001",
        subject_id="2508.00001",
        idea_method="tensor network contraction for transformer attention",
        idea_problem="protein folding structure prediction",
        target_library=DEFAULT_TARGET_LIBRARY,
        code="raise RuntimeError('boom')\n",
        context_equations=(),
        raw_response="",
    )

    fixed_response = "```python\nprint('fixed')\n```"

    def flaky_run(code: str) -> RunResult:
        if "fixed" in code:
            return RunResult(code=code, returncode=0, stdout="fixed\n", stderr="", timed_out=False)
        return RunResult(
            code=code, returncode=1, stdout="", stderr="RuntimeError: boom", timed_out=False
        )

    outcome = self_heal(generated, llm=_StubLLM([fixed_response]), run=flaky_run, max_retries=2)
    assert outcome.verdict == "repaired", outcome
    assert outcome.final_code == "print('fixed')", outcome
    assert outcome.traceback == "", outcome
    assert len(outcome.attempts) == 2, outcome

    def always_fails(code: str) -> RunResult:
        return RunResult(code=code, returncode=1, stdout="", stderr="ImportError: nope", timed_out=False)

    outcome = self_heal(
        generated, llm=_StubLLM([fixed_response, fixed_response]), run=always_fails, max_retries=2
    )
    assert outcome.verdict == "failed", outcome
    assert "ImportError: nope" in outcome.traceback, outcome
    assert len(outcome.attempts) == 3, outcome

    def always_succeeds(code: str) -> RunResult:
        return RunResult(code=code, returncode=0, stdout="ok\n", stderr="", timed_out=False)

    outcome = self_heal(generated, llm=_StubLLM([]), run=always_succeeds, max_retries=2)
    assert outcome.verdict == "validated", outcome
    assert len(outcome.attempts) == 1, outcome

    print("einstein.sandbox self-check: OK")


if __name__ == "__main__":
    _self_check()

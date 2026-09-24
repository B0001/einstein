"""One-time, hand-run script: exercise `einstein.sandbox.self_heal` against a
real `uv run --with qiskit... --with qiskit-aer...` subprocess -- the one
thing `tests/test_sandbox.py` cannot do, since it hits the network to
resolve those packages (see einstein/sandbox.py's module docstring on why
`_default_run` is never exercised by `tests/`).

There is no real LLM wired into this sandbox environment, so "feed stderr
back to the codegen agent" is demonstrated with a scripted stand-in
(`_ScriptedRepairLLM`) that returns pre-written responses in order rather
than actually reading the stderr and reasoning about it -- this script
proves the *loop* (real subprocess execution, real traceback capture, real
retry-until-success-or-exhaustion) genuinely works; it does not and cannot
demonstrate that a real model's repairs would succeed. That gap is the same
one einstein-18's handoff flagged as unverifiable for codegen itself.

Two scenarios, both against the einstein-19 acceptance criterion
("deliberately broken snippet is repaired or fails after N with the
traceback recorded"):

  1. REPAIRED -- a snippet with a `SyntaxError` (unbalanced paren), then one
     scripted response that is *still* broken (a `NameError`), then one that
     is genuinely correct Qiskit 2.5.2 code. Demonstrates multi-round repair
     against a real interpreter, not a single lucky retry.
  2. FAILED -- a snippet that imports a module that will never exist, with
     scripted "repairs" that never fix the actual problem. Demonstrates the
     N-exhausted path with `SandboxOutcome.traceback` populated from the
     real subprocess's real stderr.

Usage: uv run python scripts/sandbox_live_check.py
(Needs network the first time to fetch qiskit/qiskit-aer wheels into uv's
cache; subsequent runs are fast, ~2s, from cache.)
"""

from __future__ import annotations

from einstein.codegen import DEFAULT_TARGET_LIBRARY, GeneratedCode
from einstein.sandbox import DEFAULT_AER_VERSION, self_heal


class _ScriptedRepairLLM:
    """Returns each canned response in order. Not a real model -- see module docstring."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.prompts_seen: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts_seen.append(prompt)
        return self._responses.pop(0)


def _generated(code: str, gap_key: str) -> GeneratedCode:
    return GeneratedCode(
        gap_key=gap_key,
        subject_id="live-check",
        idea_method="Bell-pair circuit under AerSimulator",
        idea_problem="sandbox self-check, not a real gap",
        target_library=DEFAULT_TARGET_LIBRARY,
        code=code,
        context_equations=(),
        raw_response="",
    )


BROKEN_SYNTAX = (
    "from qiskit import QuantumCircuit\n"
    "from qiskit_aer import AerSimulator\n\n"
    "qc = QuantumCircuit(2)\n"
    "qc.h(0)\n"
    "qc.cx(0, 1)\n"
    "qc.measure_all()\n\n"
    "sim = AerSimulator()\n"
    "result = sim.run(qc, shots=100).result()\n"
    "print(result.get_counts()\n"  # missing close paren
)

STILL_BROKEN_NAME_ERROR = (
    "```python\n"
    "from qiskit import QuantumCircuit\n"
    "from qiskit_aer import AerSimulator\n\n"
    "qc = QuantumCircuit(2)\n"
    "qc.h(0)\n"
    "qc.cx(0, 1)\n"
    "qc.measure_all()\n\n"
    "sim = AerSimulator()\n"
    "result = sim.run(circuit, shots=100).result()\n"  # undefined name: circuit
    "print(result.get_counts())\n"
    "```"
)

FIXED = (
    "```python\n"
    "from qiskit import QuantumCircuit\n"
    "from qiskit_aer import AerSimulator\n\n"
    "qc = QuantumCircuit(2)\n"
    "qc.h(0)\n"
    "qc.cx(0, 1)\n"
    "qc.measure_all()\n\n"
    "sim = AerSimulator()\n"
    "result = sim.run(qc, shots=100).result()\n"
    "print(result.get_counts())\n"
    "```"
)

NEVER_EXISTS = "import qiskit_this_package_will_never_exist_xyz\n"

STILL_WRONG_1 = "```python\nimport qiskit_this_package_will_never_exist_xyz_v2\n```"
STILL_WRONG_2 = "```python\nimport qiskit_this_package_will_never_exist_xyz_v3\n```"


def main() -> None:
    print(f"target_library={DEFAULT_TARGET_LIBRARY!r} aer_version={DEFAULT_AER_VERSION!r}\n")

    print("=== Scenario 1: expect verdict='repaired' ===")
    llm = _ScriptedRepairLLM([STILL_BROKEN_NAME_ERROR, FIXED])
    outcome = self_heal(
        _generated(BROKEN_SYNTAX, "live-check:repaired"), llm=llm, max_retries=3
    )
    print(f"verdict={outcome.verdict!r} attempts={len(outcome.attempts)} note={outcome.note!r}")
    for i, attempt in enumerate(outcome.attempts):
        print(
            f"  attempt {i}: is_repair={attempt.is_repair} succeeded={attempt.result.succeeded} "
            f"returncode={attempt.result.returncode}"
        )
        if not attempt.result.succeeded:
            print(f"    stderr tail: {attempt.result.stderr.strip().splitlines()[-1]!r}")
    assert outcome.verdict == "repaired", outcome
    assert outcome.traceback == "", outcome
    assert "11" in outcome.attempts[-1].result.stdout or "00" in outcome.attempts[-1].result.stdout, outcome
    print()

    print("=== Scenario 2: expect verdict='failed' with traceback recorded ===")
    llm = _ScriptedRepairLLM([STILL_WRONG_1, STILL_WRONG_2])
    outcome = self_heal(_generated(NEVER_EXISTS, "live-check:failed"), llm=llm, max_retries=2)
    print(f"verdict={outcome.verdict!r} attempts={len(outcome.attempts)} note={outcome.note!r}")
    print(f"traceback (last attempt's real stderr):\n{outcome.traceback.strip()}")
    assert outcome.verdict == "failed", outcome
    assert outcome.traceback != "", "acceptance criterion requires the traceback be recorded"
    assert "ModuleNotFoundError" in outcome.traceback or "No module named" in outcome.traceback, outcome
    assert len(outcome.attempts) == 3, outcome
    print()

    print("Both scenarios matched einstein-19's acceptance criterion.")


if __name__ == "__main__":
    main()

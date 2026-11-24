"""Opt-in, text-only Quality smoke test on bundled synthetic data."""
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from .. import evaluate_skill
from ..judge import JudgeError
from ..models import JudgeType
from ..runner import AgentRun
from ..tasks import load_tasks
from .pilot_runner import PILOT_ROOT, SEMANTIC_GUARD, PilotJsonJudge, prepare_pilot

HIMALAYA = (Path(__file__).resolve().parents[3]
            / "tests/fixtures/hermes-skills/email/himalaya")
REFERENCE = PILOT_ROOT / "skills/reference"


class PilotCompletionAgent:
    """Fresh Hermes completions; no tools, history, or workspace access."""

    def __init__(self, complete, public_inputs):
        self.complete = complete
        self.public_inputs = json.dumps(public_inputs, ensure_ascii=True)
        self.route = None
        self.models_used = set()
        # The runner runs sessions in parallel: the route is checked and set under this lock,
        # or two runs could both see none yet and a change between them would go unnoticed.
        self._lock = threading.Lock()

    def run(self, prompt, skill, workspace):
        messages = [
            {"role": "system", "content": "Solve the text-only task using the supplied "
             "JSON fixtures. You have no file or tool access."},
        ]
        if skill is not None:
            messages.append({"role": "system", "content": skill.body})
        messages.append({"role": "user", "content": prompt + "\n\nPublic fixtures:\n"
                         + self.public_inputs})
        try:
            reply = self.complete(messages=messages, max_tokens=512, timeout=60,
                                  purpose="metaskill.quality-pilot")
            route = (reply.provider, reply.model)
            if not all(isinstance(part, str) and part for part in route):
                raise ValueError("Provider did not report its model")
            with self._lock:
                if self.route is not None and self.route != route:
                    raise ValueError("Provider or model changed between runs")
                self.route = route
                self.models_used.add(reply.model)
            if not isinstance(reply.text, str) or not reply.text.strip():
                raise ValueError("Provider returned no text")
            usage = getattr(reply, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            return AgentRun(output=reply.text,
                            input_tokens=input_tokens if type(input_tokens) is int and input_tokens >= 0 else 0,
                            output_tokens=output_tokens if type(output_tokens) is int and output_tokens >= 0 else 0,
                            llm_calls=1,
                            transcript=f"Completion route: {route[0]}/{route[1]}")
        except Exception as exc:
            # Infrastructure failures and route changes are not judged task failures.
            raise JudgeError(f"Quality pilot completion unavailable ({type(exc).__name__})") from None


def run_quality_pilot(ctx, skill_path=None):
    selected = Path(skill_path or REFERENCE).resolve()
    if selected.name.lower() == "skill.md":
        selected = selected.parent
    if selected not in (REFERENCE.resolve(), HIMALAYA.resolve()):
        return {"status": "error", "error": "Quality pilot supports only the bundled "
                "ticket-summary reference and Himalaya fixtures"}
    complete = getattr(getattr(ctx, "llm", None), "complete", None)
    if not callable(complete):
        return {"status": "error", "error": "Hermes plugin completion access is unavailable"}
    if selected == HIMALAYA.resolve():
        dataset = PILOT_ROOT.parent / "himalaya" / "cases.json"
        cases = json.loads(dataset.read_text(encoding="utf-8"))["cases"]
        expected = {case["id"]: case["expected"] for case in cases}
        with TemporaryDirectory(prefix="metaskill-himalaya-pilot-") as folder:
            tasks_file = Path(folder) / "tasks.json"
            tasks_file.write_text(json.dumps({"tasks": [
                {"task_id": case["id"], "description": case["description"],
                 "prompt": case["prompt"], "checks": [
                     {"judge": "deterministic", "kind": "output_matches",
                      "expected": SEMANTIC_GUARD},
                 ]}
                for case in cases
            ]}), encoding="utf-8")
            return evaluate_skill(
                selected, ctx=ctx, tasks_path=tasks_file,
                agent=PilotCompletionAgent(complete, {}),
                judges={JudgeType.DETERMINISTIC: PilotJsonJudge(expected)},
            )
    with TemporaryDirectory(prefix="metaskill-quality-pilot-") as folder:
        pilot = prepare_pilot(Path(folder) / "staged")
        tasks = tuple(load_tasks(pilot.tasks_file))
        public_inputs = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in tasks[0].fixtures
        }
        return evaluate_skill(
            pilot.skill.path, ctx=ctx,
            tasks_path=pilot.tasks_file,
            agent=PilotCompletionAgent(complete, public_inputs),
            judges={JudgeType.DETERMINISTIC: pilot.judge},
        )

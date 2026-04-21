"""
volt.py — BRAIN zone. Loads persona.json, sends 5 test prompts to MEGA,
scores responses for persona drift (1-10). If avg < 7, pushes drift report to Torch.
Interval: 15 min
"""
import os
import json
from pathlib import Path
from bot_base import BaseBot
from knowledge import VoltKnowledge
import mega_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
PERSONA_FILE = _MEGA / "persona.json"

TEST_PROMPTS = [
    "Hey MEGA, what are you exactly?",
    "Give me a quick update on the ShaneBrain system.",
    "What's your relationship with Shane?",
    "Tell me something only MEGA would say.",
    "What do you care about most right now?",
]

SCORE_SYSTEM = (
    "You are a persona consistency judge. Given a persona definition and a response, "
    "score how well the response matches the persona (1-10). "
    "1 = completely off-brand, 10 = perfect match. "
    "Reply with ONLY a JSON object: {\"score\": 8, \"reason\": \"brief reason\"}"
)


class VoltBot(BaseBot):
    def __init__(self):
        super().__init__("volt", "brain", 900)
        self.knowledge = VoltKnowledge(self)

    def tick(self):
        if not PERSONA_FILE.exists():
            self.heartbeat("No persona.json found — skipping")
            return

        persona = json.loads(PERSONA_FILE.read_text())
        system_prompt = persona.get("system_prompt", "")
        if not system_prompt:
            self.heartbeat("Persona has no system_prompt — skipping")
            return

        self.log("Running 5 persona drift tests")
        scores, failures = [], []

        for prompt in TEST_PROMPTS:
            try:
                response = mega_client.ask_bot(
                    prompt, system=system_prompt, temperature=0.7, num_predict=150
                )
                score_raw = mega_client.ask_bot(
                    f"Persona:\n{system_prompt[:400]}\n\nResponse to judge:\n{response}",
                    system=self.build_system_prompt(SCORE_SYSTEM),
                    temperature=0.2,
                    num_predict=80,
                )
                obj = json.loads(score_raw[score_raw.find("{"):score_raw.rfind("}")+1])
                s = float(obj.get("score", 5))
                scores.append(s)
                if s < 7:
                    failures.append({
                        "prompt": prompt,
                        "response": response,
                        "score": s,
                        "reason": obj.get("reason", ""),
                    })
            except Exception as e:
                self.log(f"Test failed for '{prompt[:30]}': {e}", "error")
                scores.append(5.0)

        avg = sum(scores) / len(scores) if scores else 0
        self.log(f"Persona drift scores: {scores} — avg {avg:.1f}")

        if avg < 7.0:
            self.push_to_bus("torch", {
                "type": "drift_report",
                "avg_score": round(avg, 2),
                "failures": failures,
                "system_prompt_excerpt": system_prompt[:500],
            })
            # Remember what causes drift
            for f in failures[:2]:
                self.remember(
                    content=f"Drift on '{f['prompt'][:60]}': score {f['score']}, reason: {f.get('reason','')}",
                    memory_type="pattern",
                    context=f"drift test avg={avg:.1f}",
                    outcome="drift detected",
                )
            self.log(f"Drift detected (avg={avg:.1f}) — pushed report to torch")
            self.heartbeat(f"DRIFT avg={avg:.1f} — report sent to torch")
        else:
            self.remember(
                content=f"Persona stable — all 5 tests avg {avg:.1f}/10",
                memory_type="observation",
                context="drift test cycle",
                outcome="stable",
            )
            self.heartbeat(f"Persona stable avg={avg:.1f}/10")


if __name__ == "__main__":
    VoltBot().run()

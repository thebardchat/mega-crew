"""
torch.py — LEFT HAND zone. Consumes Volt drift reports.
Uses llama3.1:8b to suggest system prompt edits. Pushes proposed changes to Arc.
Never writes directly to persona.json.
Interval: 3 min (reactive consumer)
"""
from bot_base import BaseBot
from knowledge import TorchKnowledge
import mega_client

SUGGEST_SYSTEM = (
    "You are a persona architect for an AI named MEGA-SHANEBRAIN. "
    "You've been given a drift report showing where MEGA is going off-brand. "
    "Suggest SPECIFIC edits to the system prompt that would fix the drift. "
    "Be surgical — propose only the sentences to add/change, not a full rewrite. "
    "Reply with JSON: {\"proposed_additions\": [\"...\"], \"proposed_removals\": [\"...\"], "
    "\"rationale\": \"brief explanation\"}"
)


class TorchBot(BaseBot):
    def __init__(self):
        super().__init__("torch", "leftHand", 180)
        self.knowledge = TorchKnowledge(self)

    def tick(self):
        messages = self.pull_from_bus()
        if not messages:
            self.heartbeat("Queue empty — waiting for drift reports")
            return

        # Process any arc rejections first — learn from feedback
        rejections, messages = self.process_rejections(messages)
        if rejections:
            self.log(f"Received {len(rejections)} arc rejections — learning from feedback")

        for msg in messages:
            payload = msg["payload"]
            if payload.get("type") != "drift_report":
                continue

            avg_score = payload.get("avg_score", 0)
            failures  = payload.get("failures", [])
            excerpt   = payload.get("system_prompt_excerpt", "")

            self.log(f"Processing drift report — avg_score={avg_score}, {len(failures)} failures")

            failure_text = "\n".join([
                f"- Prompt: {f['prompt']}\n  Response: {f['response'][:100]}\n  Score: {f['score']} — {f['reason']}"
                for f in failures[:5]
            ])

            prompt = (
                f"Current system prompt excerpt:\n{excerpt}\n\n"
                f"Drift failures (avg score {avg_score}/10):\n{failure_text}\n\n"
                "Suggest surgical edits to fix this drift."
            )

            try:
                raw = mega_client.ask_bot(
                    prompt, system=self.build_system_prompt(SUGGEST_SYSTEM), temperature=0.4, num_predict=400
                )
                start = raw.find("{")
                end   = raw.rfind("}") + 1
                suggestion = {"raw": raw} if start < 0 else __import__("json").loads(raw[start:end])
            except Exception as e:
                self.log(f"Suggestion generation failed: {e}", "error")
                suggestion = {"error": str(e)}

            self.push_to_bus("arc", {
                "type":        "persona_edit_proposal",
                "source":      "torch",
                "avg_score":   avg_score,
                "suggestion":  suggestion,
                "failure_count": len(failures),
            })
            self.remember(
                content=f"Proposed persona edit for drift avg={avg_score}: {suggestion.get('rationale', suggestion.get('error', 'no rationale'))[:120]}",
                memory_type="decision",
                context=f"{len(failures)} failures triggered this proposal",
                outcome="sent to arc",
            )
            self.log(f"Persona edit proposal sent to Arc (drift avg={avg_score})")

        self.heartbeat(f"Processed {len(messages)} drift reports → arc")


if __name__ == "__main__":
    TorchBot().run()

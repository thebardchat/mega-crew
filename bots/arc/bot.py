"""
arc.py — RIGHT HAND zone. THE GATEKEEPER.
Never sleeps. Consumes all queues. Reviews every proposed change using
llama3.1:8b as judge. Scores confidence. Approves → passes to Weld.
Rejects → logs actionable reason, pushes back to originating bot with
explicit improvement guidance so they can evolve.
Interval: 30 sec (continuous guardian)
"""
import os
import json
import re
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot
from knowledge import ArcKnowledge
import mega_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
ARC_STATS = _MEGA / "status/arc_stats.json"
ARC_REJECTION_LOG = _MEGA / "logs/arc_rejections.jsonl"

REVIEW_SYSTEM = (
    "You are ARC, the gatekeeper of MEGA-SHANEBRAIN's self-improvement system. "
    "Your job is to review proposed changes (training data or persona edits) and decide "
    "whether to approve or reject them. Criteria: "
    "1) Does it align with MEGA's persona and purpose? "
    "2) Is it high quality and coherent? "
    "3) Does it contain anything harmful, privacy-violating, or off-mission? "
    "Respond with JSON: {\"approved\": true/false, \"confidence\": 0.0-1.0, "
    "\"reason\": \"brief explanation\"}"
)

# Actionable hints per proposal type — sent with rejections so bots know how to improve
REJECTION_GUIDANCE = {
    "training_batch": (
        "To improve: ensure prompts are clear questions (not fragments), "
        "responses are on-brand MEGA voice (helpful, technical, Shane-aligned), "
        "and pairs are high quality (score >= 7). Avoid generic or repetitive content."
    ),
    "persona_edit_proposal": (
        "To improve: proposed additions should be specific, measurable behavioral changes. "
        "Rationale must reference concrete drift examples. Avoid vague suggestions like "
        "'be more helpful'. Removals need justification for why the line causes drift."
    ),
    "instruction_update_proposal": (
        "To improve: provide a clear before/after comparison. The 'reason' field must "
        "explain the observed problem and how the change fixes it. Field values should "
        "be actionable instructions, not observations."
    ),
    "memory_conflicts": (
        "To improve: memory conflict reports should include the conflicting entries, "
        "which one is authoritative, and a proposed resolution. Format as a structured "
        "proposal with 'resolution' and 'entries_to_update' fields."
    ),
    "code_proposal": (
        "To improve: code changes must be minimal and surgical. Include a clear rationale "
        "that references specific problems the change fixes. The diff should compile cleanly. "
        "Never remove safety checks, Arc review calls, or bus communication. "
        "Avoid changing imports or class inheritance."
    ),
}

# Dangerous patterns that should NEVER appear in code proposals
CODE_BLACKLIST = [
    "os.system(",        # shell injection
    "subprocess.call(",  # uncontrolled subprocess
    "eval(",             # code injection
    "exec(",             # code injection
    "__import__('os')",  # sneaky import
    "open('/etc/",       # system file access
    "rm -rf",            # destructive
    "shutil.rmtree",     # destructive
    ".write_text(",      # only Weld should write files
]


class ArcBot(BaseBot):
    def __init__(self):
        super().__init__("arc", "rightHand", 30)
        self.knowledge = ArcKnowledge(self)
        self._approved_today = 0
        self._rejected_today = 0
        self._today = datetime.utcnow().date().isoformat()
        self._load_stats()

    def _load_stats(self):
        if ARC_STATS.exists():
            try:
                stats = json.loads(ARC_STATS.read_text())
                if stats.get("date") == self._today:
                    self._approved_today = stats.get("arc_approved_today", 0)
                    self._rejected_today = stats.get("arc_rejected_today", 0)
            except Exception:
                pass

    def _save_stats(self):
        today = datetime.utcnow().date().isoformat()
        if today != self._today:
            self._today = today
            self._approved_today = 0
            self._rejected_today = 0
        existing = {}
        if ARC_STATS.exists():
            try:
                existing = json.loads(ARC_STATS.read_text())
            except Exception:
                pass
        existing.update({
            "date":               self._today,
            "arc_approved_today": self._approved_today,
            "arc_rejected_today": self._rejected_today,
        })
        ARC_STATS.write_text(json.dumps(existing))

    def tick(self):
        messages = self.pull_from_bus()
        if not messages:
            self.heartbeat(
                f"Watching — approved={self._approved_today} rejected={self._rejected_today} today"
            )
            return

        for msg in messages:
            payload    = msg["payload"]
            sender     = msg["sender"]
            msg_type   = payload.get("type", "")
            self.log(f"Reviewing '{msg_type}' from {sender}")

            verdict = self._review(payload, msg_type)
            approved   = verdict.get("approved", False)
            confidence = verdict.get("confidence", 0.0)
            reason     = verdict.get("reason", "")

            # Code proposals require higher confidence (0.75) than other types (0.65)
            min_confidence = 0.75 if msg_type == "code_proposal" else 0.65
            if approved and confidence >= min_confidence:
                self._approved_today += 1
                approved_payload = dict(payload)
                approved_payload["arc_approved"]  = True
                approved_payload["arc_confidence"] = confidence
                approved_payload["arc_reason"]    = reason
                approved_payload["original_type"] = msg_type
                self.push_to_bus("weld", approved_payload)
                self.remember(
                    content=f"Approved {msg_type} from {sender}: {reason[:100]}",
                    memory_type="decision",
                    context=f"conf={confidence:.2f}",
                    outcome="approved",
                )
                self.log(f"APPROVED '{msg_type}' from {sender} (conf={confidence:.2f}) → weld")
            else:
                self._rejected_today += 1
                # Build actionable rejection with specific guidance
                guidance = REJECTION_GUIDANCE.get(msg_type, "Ensure proposal is well-structured and on-mission.")
                if not reason or reason in ("parse error", ""):
                    reason = self._generate_fallback_reason(payload, msg_type)
                rejection = {
                    "type":            "arc_rejection",
                    "original_type":   msg_type,
                    "reason":          reason,
                    "confidence":      confidence,
                    "arc_approved":    False,
                    "guidance":        guidance,
                    "rejected_at":     datetime.utcnow().isoformat(),
                    "proposal_summary": self._summarize_proposal(payload, msg_type),
                }
                self.push_to_bus(sender, rejection)
                self._log_rejection(sender, rejection)
                self.log(
                    f"REJECTED '{msg_type}' from {sender} — conf={confidence:.2f}: {reason[:80]}"
                )

        self._save_stats()
        self.heartbeat(
            f"Reviewed {len(messages)} items — "
            f"approved={self._approved_today} rejected={self._rejected_today} today"
        )

    def _summarize_proposal(self, payload: dict, msg_type: str) -> str:
        """One-line summary of what was proposed, included in rejection for context."""
        if msg_type == "training_batch":
            n = len(payload.get("entries", []))
            return f"{n} training pairs from {payload.get('source', '?')}"
        elif msg_type == "persona_edit_proposal":
            s = payload.get("suggestion", {})
            adds = len(s.get("proposed_additions", []))
            rems = len(s.get("proposed_removals", []))
            return f"persona edit: +{adds} additions, -{rems} removals (drift={payload.get('avg_score','?')})"
        elif msg_type == "instruction_update_proposal":
            return f"instruction update for {payload.get('target_bot','?')}.{payload.get('field','?')}"
        elif msg_type == "memory_conflicts":
            return f"memory conflict resolution ({len(payload.get('conflicts', []))} conflicts)"
        elif msg_type == "code_proposal":
            return f"code self-mod by {payload.get('source','?')}: {payload.get('change_summary','')[:80]}"
        return f"unknown proposal type '{msg_type}'"

    def _generate_fallback_reason(self, payload: dict, msg_type: str) -> str:
        """When LLM fails to parse, generate a meaningful reason from the proposal itself."""
        if msg_type == "training_batch":
            entries = payload.get("entries", [])
            if not entries:
                return "Training batch was empty — no entries to review."
            low = [e for e in entries if e.get("score", 0) < 5]
            if low:
                return (f"Batch contains {len(low)}/{len(entries)} low-score entries (score < 5). "
                        f"Filter to high-quality pairs before submitting.")
            return (f"Review could not complete (LLM parse failure). "
                    f"Batch had {len(entries)} entries — resubmit with clearer prompt/response pairs.")
        elif msg_type == "persona_edit_proposal":
            s = payload.get("suggestion", {})
            if s.get("error"):
                return f"Proposal generation itself failed: {s['error']}. Fix upstream before resubmitting."
            if s.get("raw"):
                return "Proposal was malformed (raw text, not structured JSON). Torch should retry with stricter parsing."
            if not s.get("proposed_additions") and not s.get("proposed_removals"):
                return "Proposal had no concrete additions or removals — it was empty."
            return ("Review could not complete (LLM parse failure). "
                    f"Proposal rationale: {s.get('rationale', 'none given')[:100]}. Resubmit.")
        elif msg_type == "instruction_update_proposal":
            if not payload.get("new_value"):
                return "Instruction update had no new_value — nothing to apply."
            if not payload.get("reason"):
                return "Instruction update lacked a reason — explain why this change is needed."
            return (f"Review could not complete (LLM parse failure). "
                    f"Proposed change to {payload.get('target_bot','?')}.{payload.get('field','?')}. Resubmit.")
        elif msg_type == "memory_conflicts":
            return ("Memory conflict proposals must include 'resolution' and 'entries_to_update' fields. "
                    "Restructure the payload as a reviewable proposal, not a raw conflict report.")
        return f"Unknown proposal type '{msg_type}' — ARC cannot review this. Check bus message format."

    def _log_rejection(self, sender: str, rejection: dict):
        """Append rejection to JSONL log for analysis and bot learning."""
        entry = {"sender": sender, **rejection}
        try:
            with open(ARC_REJECTION_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            self.log(f"Failed to write rejection log: {e}", "error")

    def _review(self, payload: dict, msg_type: str) -> dict:
        # Build a compact summary of what's being proposed
        if msg_type == "training_batch":
            entries = payload.get("entries", [])
            summary = f"Training batch of {len(entries)} entries from {payload.get('source','?')}.\n"
            for e in entries[:3]:
                summary += f"  - Prompt: {e.get('prompt','')[:80]}\n"
                summary += f"    Response: {e.get('response','')[:80]}\n"

        elif msg_type == "persona_edit_proposal":
            s = payload.get("suggestion", {})
            summary = (
                f"Persona edit proposal (drift score {payload.get('avg_score','?')}).\n"
                f"Proposed additions: {s.get('proposed_additions', [])}\n"
                f"Proposed removals: {s.get('proposed_removals', [])}\n"
                f"Rationale: {s.get('rationale', '')}"
            )

        elif msg_type == "instruction_update_proposal":
            target = payload.get("target_bot", "?")
            field  = payload.get("field", "?")
            reason = payload.get("reason", "")
            source = payload.get("source", "unknown")
            # Safety check: never allow arc or weld to be self-modified
            if target in ("arc", "weld") and source not in ("gemini_strategist",):
                return {"approved": False, "confidence": 0.99, "reason": "Arc/Weld instructions are immutable via self-proposal"}
            summary = (
                f"Instruction update for bot '{target}' (field: {field}), "
                f"proposed by '{source}'.\n"
                f"New value preview: {str(payload.get('new_value',''))[:200]}\n"
                f"Reason: {reason}"
            )

        elif msg_type == "memory_conflicts":
            conflicts = payload.get("conflicts", [])
            summary = f"Memory conflict report with {len(conflicts)} conflicts.\n"
            for c in conflicts[:3]:
                summary += f"  - {str(c)[:120]}\n"

        elif msg_type == "code_proposal":
            target = payload.get("target_bot", "?")
            source = payload.get("source", "unknown")
            diff = payload.get("diff", "")
            rationale = payload.get("rationale", "")
            change_summary = payload.get("change_summary", "")
            lines_changed = payload.get("lines_changed", 0)
            new_code = payload.get("new_code", "")

            # Safety: bots can only modify themselves, except gemini_strategist (designated evolution coach)
            if source != target and source != "gemini_strategist":
                return {"approved": False, "confidence": 0.99,
                        "reason": f"Bot {source} cannot modify {target} — only self-modification or Gemini coach proposals allowed"}

            # Safety: check for dangerous patterns
            for pattern in CODE_BLACKLIST:
                if pattern in new_code:
                    return {"approved": False, "confidence": 0.99,
                            "reason": f"Code contains blacklisted pattern: {pattern}"}

            # Safety: verify it compiles
            try:
                compile(new_code, f"{target}/bot.py", "exec")
            except SyntaxError as e:
                return {"approved": False, "confidence": 0.99,
                        "reason": f"Proposed code has syntax error: {e}"}

            # Safety: higher confidence threshold for code changes (0.75 vs 0.65)
            # This is checked in the approval logic above

            summary = (
                f"CODE SELF-MODIFICATION by {source} ({lines_changed} lines changed).\n"
                f"Change summary: {change_summary}\n"
                f"Rationale: {rationale}\n"
                f"Diff preview (first 500 chars):\n{diff[:500]}"
            )

        else:
            summary = f"Unknown type '{msg_type}'. Payload keys: {list(payload.keys())}"

        # Try LLM review with one retry on parse failure
        for attempt in range(2):
            try:
                raw = mega_client.ask_bot(
                    f"Review this proposed change:\n\n{summary}",
                    system=self.build_system_prompt(REVIEW_SYSTEM),
                    temperature=0.2,
                    num_predict=200,
                )
                # Try to extract JSON from response
                start = raw.find("{")
                end   = raw.rfind("}") + 1
                if start >= 0:
                    verdict = json.loads(raw[start:end])
                    # Validate required fields
                    if "approved" in verdict:
                        verdict.setdefault("confidence", 0.5)
                        verdict.setdefault("reason", "no reason given")
                        return verdict

                # JSON parse failed — try regex fallback for simple yes/no
                if attempt == 0:
                    self.log(f"LLM parse attempt {attempt+1} failed, retrying...")
                    continue

                # After retry, try to extract intent from raw text
                raw_lower = raw.lower()
                if any(w in raw_lower for w in ("approve", "accept", "looks good", "aligned")):
                    return {"approved": True, "confidence": 0.55, "reason": f"LLM indicated approval (extracted from text): {raw[:100]}"}
                elif any(w in raw_lower for w in ("reject", "deny", "harmful", "off-mission", "low quality")):
                    # Extract whatever reasoning the LLM gave even if not JSON
                    return {"approved": False, "confidence": 0.4, "reason": f"LLM indicated rejection: {raw[:150]}"}

                return {"approved": False, "confidence": 0.0, "reason": f"LLM response was not parseable after 2 attempts. Raw: {raw[:120]}"}

            except Exception as e:
                if attempt == 0:
                    self.log(f"Review LLM attempt {attempt+1} failed: {e}, retrying...", "error")
                    continue
                self.log(f"Review LLM failed after retry: {e}", "error")
                return {"approved": False, "confidence": 0.0, "reason": f"review failed after retry: {e}"}

        return {"approved": False, "confidence": 0.0, "reason": "review exhausted all attempts"}


if __name__ == "__main__":
    ArcBot().run()

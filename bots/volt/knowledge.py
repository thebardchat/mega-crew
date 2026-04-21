"""Volt knowledge layer — drift detection insights."""

from _knowledge_template import BotKnowledge


class VoltKnowledge(BotKnowledge):

    def get_drift_history(self, limit=10):
        """Get past drift detections and corrections."""
        return self.bot.recall("drift detected persona deviation", limit=limit, memory_type="observation")

    def get_stable_prompts(self, limit=10):
        """Get prompts that consistently stayed on-persona."""
        return self.bot.recall("stable consistent on-persona prompt", limit=limit, memory_type="pattern")

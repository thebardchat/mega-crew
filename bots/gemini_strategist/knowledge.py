"""Gemini Strategist knowledge layer — growth coaching insights."""

from _knowledge_template import BotKnowledge


class GeminiStrategistKnowledge(BotKnowledge):

    def get_coaching_history(self, limit=10):
        """Get past coaching sessions and strategic advice given."""
        return self.bot.recall("coaching advice strategy recommendation", limit=limit, memory_type="decision")

    def get_growth_metrics(self, limit=10):
        """Get growth metrics and progress tracking data."""
        return self.bot.recall("growth metric progress milestone improvement", limit=limit, memory_type="observation")

"""Sparky knowledge layer — quality judge scoring insights."""

from _knowledge_template import BotKnowledge


class SparkyKnowledge(BotKnowledge):

    def get_high_scoring_pairs(self, limit=10):
        """Get prompt/response pairs that scored highest."""
        return self.bot.recall("high quality score prompt response", limit=limit, memory_type="pattern")

    def get_scoring_trends(self, limit=10):
        """Get trends in quality scores over time."""
        return self.bot.recall("scoring trend quality improvement decline", limit=limit, memory_type="observation")

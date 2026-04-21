"""Grind knowledge layer — re-embedding insights."""

from _knowledge_template import BotKnowledge


class GrindKnowledge(BotKnowledge):

    def get_reembedding_history(self, limit=10):
        """Get history of re-embedding runs and stale vector refreshes."""
        return self.bot.recall("reembedding refresh stale vector updated", limit=limit, memory_type="pattern")

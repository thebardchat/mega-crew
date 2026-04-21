"""Rivet knowledge layer — dedup and formatting insights."""

from _knowledge_template import BotKnowledge


class RivetKnowledge(BotKnowledge):

    def get_dedup_stats(self, limit=10):
        """Get deduplication results and duplicate counts."""
        return self.bot.recall("duplicates removed dedup merge", limit=limit, memory_type="observation")

    def get_batch_history(self, limit=10):
        """Get history of formatting and dedup batch runs."""
        return self.bot.recall("batch run format dedup processing", limit=limit, memory_type="pattern")

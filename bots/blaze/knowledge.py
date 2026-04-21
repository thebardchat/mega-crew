"""Blaze knowledge layer — context injection insights."""

from _knowledge_template import BotKnowledge


class BlazeKnowledge(BotKnowledge):

    def get_injection_history(self, limit=10):
        """Get history of context injections and their effectiveness."""
        return self.bot.recall("context injected augmented enriched", limit=limit, memory_type="decision")

    def get_source_stats(self, limit=10):
        """Get stats on which knowledge sources are most used."""
        return self.bot.recall("source weaviate collection retrieval frequency", limit=limit, memory_type="pattern")

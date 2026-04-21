"""Neon knowledge layer — fast consumer embedding insights."""

from _knowledge_template import BotKnowledge


class NeonKnowledge(BotKnowledge):

    def get_embedding_stats(self, limit=10):
        """Get embedding generation stats — counts, speed, failures."""
        return self.bot.recall("embedding generated vector consumed batch", limit=limit, memory_type="pattern")

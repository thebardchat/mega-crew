"""Bolt knowledge layer — pattern analysis insights."""

from _knowledge_template import BotKnowledge


class BoltKnowledge(BotKnowledge):

    def get_topic_trends(self, limit=10):
        """Get trending topics and frequency patterns."""
        return self.bot.recall("topic trend frequency rising", limit=limit, memory_type="pattern")

    def get_weak_topics(self, limit=10):
        """Get topics with weak coverage or low quality."""
        return self.bot.recall("weak topic gap low coverage", limit=limit, memory_type="observation")

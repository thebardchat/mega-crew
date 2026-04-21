"""Flux knowledge layer — health monitoring insights."""

from _knowledge_template import BotKnowledge


class FluxKnowledge(BotKnowledge):

    def get_health_alerts(self, limit=10):
        """Get past health alerts and system issues."""
        return self.bot.recall("health alert warning error outage", limit=limit, memory_type="observation")

    def get_uptime_patterns(self, limit=10):
        """Get patterns in system uptime and failure windows."""
        return self.bot.recall("uptime downtime availability pattern", limit=limit, memory_type="pattern")

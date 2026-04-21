"""Spike knowledge layer — IQ benchmarking insights."""

from _knowledge_template import BotKnowledge


class SpikeKnowledge(BotKnowledge):

    def get_iq_trends(self, limit=10):
        """Get IQ score trends over time."""
        return self.bot.recall("IQ score benchmark trend improvement", limit=limit, memory_type="observation")

    def get_benchmark_history(self, limit=10):
        """Get history of benchmark test runs and results."""
        return self.bot.recall("benchmark test run result pass fail", limit=limit, memory_type="pattern")

"""Glitch knowledge layer — adversarial testing insights."""

from _knowledge_template import BotKnowledge


class GlitchKnowledge(BotKnowledge):

    def get_vulnerabilities_found(self, limit=10):
        """Get vulnerabilities and weaknesses discovered through testing."""
        return self.bot.recall("vulnerability weakness exploit found", limit=limit, memory_type="observation")

    def get_attack_history(self, limit=10):
        """Get history of adversarial test runs and outcomes."""
        return self.bot.recall("adversarial attack test jailbreak result", limit=limit, memory_type="pattern")

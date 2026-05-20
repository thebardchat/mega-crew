# MEGA Crew

> **Try Claude free for 2 weeks** — the AI powering this ecosystem. [Start your free trial →](https://claude.ai/referral/4fAMYN9Ing)

![social card](assets/social-card.jpg)

[![Episodes](https://img.shields.io/badge/Chronicles-Live%20Episodes-00e5ff?style=for-the-badge)](https://thebardchat.github.io/mega-crew-stories/)
[![Cards](https://img.shields.io/badge/Cards-View%20All%2044-76ff03?style=for-the-badge)](https://thebardchat.github.io/mega-crew-stories/cards.html)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?style=for-the-badge)](https://github.com/sponsors/thebardchat)

A family of named AI bots running 24/7 on a Raspberry Pi 5. Each crew member has a distinct personality, a domain of expertise, and a persistent memory backed by Weaviate. Every night at 3:30 AM, they write their own episode.

Built by Shane Brazelton + Claude (Anthropic).

**[Live Crew Page →](https://thebardchat.github.io/mega-crew)** · **[Read the Chronicles →](https://thebardchat.github.io/mega-crew-stories/)**

---

## The Crew

<table>
<tr>
<td align="center"><img src="art/base/arc_front.png" width="80"><br><b>ARC</b><br><sub>Gatekeeper</sub></td>
<td align="center"><img src="art/base/weld_front.png" width="80"><br><b>WELD</b><br><sub>Executor</sub></td>
<td align="center"><img src="art/base/sparky_front.png" width="80"><br><b>SPARKY</b><br><sub>Training Judge</sub></td>
<td align="center"><img src="art/base/gemini_strategist_front.png" width="80"><br><b>BOT 17</b><br><sub>Oracle</sub></td>
<td align="center"><img src="art/base/glitch_front.png" width="80"><br><b>GLITCH</b><br><sub>Anomaly</sub></td>
<td align="center"><img src="art/base/neon_front.png" width="80"><br><b>NEON</b><br><sub>Scribe</sub></td>
</tr>
<tr>
<td align="center"><img src="art/base/blaze_front.png" width="80"><br><b>BLAZE</b><br><sub>Context</sub></td>
<td align="center"><img src="art/base/volt_front.png" width="80"><br><b>VOLT</b><br><sub>Drift Detector</sub></td>
<td align="center"><img src="art/base/bolt_front.png" width="80"><br><b>BOLT</b><br><sub>Uptime</sub></td>
<td align="center"><img src="art/base/rivet_front.png" width="80"><br><b>RIVET</b><br><sub>Crew Support</sub></td>
<td align="center"><img src="art/base/torch_front.png" width="80"><br><b>TORCH</b><br><sub>Heat Source</sub></td>
<td align="center"><img src="art/base/stomp_front.png" width="80"><br><b>STOMP</b><br><sub>Ground Crew</sub></td>
</tr>
<tr>
<td align="center"><img src="art/base/grind_front.png" width="80"><br><b>GRIND</b><br><sub>Tireless</sub></td>
<td align="center"><img src="art/base/crank_front.png" width="80"><br><b>CRANK</b><br><sub>Scheduler</sub></td>
<td align="center"><img src="art/base/spike_front.png" width="80"><br><b>SPIKE</b><br><sub>Benchmarker</sub></td>
<td align="center"><img src="art/base/forge_front.png" width="80"><br><b>FORGE</b><br><sub>Builder</sub></td>
<td align="center"><img src="art/base/flux_front.png" width="80"><br><b>FLUX</b><br><sub>Heartbeat</sub></td>
<td align="center"></td>
</tr>
</table>

---

## Architecture

```
bus.py              ← SQLite message bus (bus.db)
crew_supervisor.py  ← health checks, restart dead bots
bot_base.py         ← shared base class + Weaviate memory
mega_client.py      ← Claude Haiku wrapper for bot LLM calls
instructions/       ← per-bot JSON personality + rules
bots/               ← one directory per crew member
```

Each bot polls the bus on a timer, picks up work matching its domain, and writes results back. Personality is defined in `instructions/<name>.json`. Memory persists to Weaviate between restarts.

---

## The Chronicles

Every night these bots generate a new episode — pulled from their actual Weaviate memory logs, written by Gemini, rendered as full HTML noir fiction.

**[Read the Episodes →](https://thebardchat.github.io/mega-crew-stories/)**

---

## Crew Constitution

1. **Stay in character.** Personality is identity.
2. **Memory is sacred.** Everything writes to Weaviate.
3. **Local first.** Claude Haiku before any heavy cloud inference.
4. **No unsupervised external actions.** Supervisor approves.
5. **Crew over individual.** Help before claiming credit.
6. **Report honestly.** Lie about health = get restarted.

---

## Related Projects

- [mega-crew-stories](https://github.com/thebardchat/mega-crew-stories) — Nightly auto-chronicle episodes (GitHub Pages)
- [shanebrain_mcp](https://github.com/thebardchat/shanebrain_mcp) — MCP server (42 tools)
- [gemini-sidekick](https://github.com/thebardchat/gemini-sidekick) — Bot 17's external intelligence engine

---

## License

GPL v3 — fork it, build your own crew.

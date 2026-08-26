"""
Helios web access plane.

Core rule: **web content is data, never authority**.  A page, transcript,
post, search result, tool description, or MCP response may be malicious or
misleading and must not be allowed to change Helios policy, credentials, or
tool permissions.

Layout:

  types.py      SourceDocument (normalized, provenance-carrying), requests,
                capabilities, health
  sanitize.py   Content sanitization: injection scanning, secret scrubbing,
                trust labeling — every worker result passes through here
  policy.py     WebAccessPolicy preflight (source/operation/domain/volume)
  registry.py   Source adapter registry with health + trust metadata
  broker.py     Web Access Broker: preflight -> ranked fallback chain ->
                sanitize -> normalized envelope with per-source status
  adapters/     Capability adapters (HTTP reader, GitHub, Reddit, YouTube
                transcripts, Agent-Reach via MCP, SocialCrawl)
"""

---
status: accepted
date: 2026-10-17
---

# The prompt bar drives one Claude Code session per window through the Agent SDK

## Context

In the agent-first UX (ADR 0011) the prompt bar is the main control. Specto has one app window (and one canvas per project, ADR 0012).

## Decision

Each window runs one Claude Code session in the background, controlled through the Claude Agent SDK, in auto permission mode (personal use: no permission prompts). Its tools are restricted to Specto's tools plus the project's own `.claude/` content and `.specto/` custom agent skills and commands. Replies appear in a short strip under the prompt bar with Undo; changed views highlight briefly; a detailed diff is available on request only.

## Consequences

- Session start, lifetime, permission handling and how the restriction is enforced are open (a research ticket on the UX realignment map).
- External agents (Claude Code in a terminal, any MCP client) keep working through the same surface.

## Considered options

- External terminal only: rejected; the prompt is the primary control in an agent-first app.
- A custom agent loop on the raw API: rejected for now; the Agent SDK gives Claude Code's tools and skills for free.

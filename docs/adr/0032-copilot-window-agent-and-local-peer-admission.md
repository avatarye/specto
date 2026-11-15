---
status: accepted
date: 2026-11-14
---

# The window agent uses Copilot; local peers enter through Specto's policy

Amends ADR 0015's provider choice. The canvas, prompt turn and external endpoint
contracts do not change.

## Decision

- The default is `CopilotAgentSession`, using `github-copilot-sdk` 1.0.34 and an
  installed, signed-in GitHub Copilot CLI. The adapter uses the SDK's stdio runtime
  connection, streaming session events, custom tools, terminal-tool success and abort.
  A local RPC heartbeat reports a lost runtime even when no final idle event arrives.
  Prompt turns own startup so Stop also cancels the first connection. Reusing an idle
  session first checks connectivity and reconnects/resumes a lost runtime.
  `SPECTO_COPILOT_CLI` selects a particular executable. `--agent claude` retains the
  legacy adapter; scripted agents and external clients are unchanged.
- Only source-qualified Specto custom tools are available. No shell, file editor,
  built-in MCP tools, implicit project/home instructions, repository file hooks or memory are
  enabled. Project `.claude/` and `.specto/` skills are loaded as text; their slash
  commands are expanded by Specto, with argument substitution but no shell evaluation.
  Skills and commands reload with resume after changes. They cannot grant tools or
  approval. Copilot chooses its available model with `auto`.
- Copilot conversation identity is stored separately in
  `.specto/copilot-agent-session.json`, tagged with the provider. Legacy Claude
  identities are neither resumed nor overwritten by Copilot. New session deletes only
  the selected provider's pointer. Resume reattaches tools and hooks and never
  automatically continues pending work. A failed resume is reported, not silently
  discarded; New session is the explicit way to forget it.
- The window opts into the SDK's live `session.rpc.management.configure` policy.
  It does not change global CLI settings. Creation and closing of other sessions are
  disabled. `copilot_sessions` discovers other opted-in, live local sessions sharing
  the Copilot configuration directory; `copilot_message` uses the provider's message
  transport, including ordinary terminal sessions. This is not a Specto-only registry
  and does not edit session journals.
- The pre-prompt hook admits exactly the adapter's pending prompt once. All other
  prompts, including provider-delivered peer text, are blocked before model execution
  and handed to `PromptBar.receive_peer`. Auto runs a labelled prompt turn, ask waits
  for Run, off records refusal. Sender names and IDs are claims, never human
  authorization; link-code approval still requires the user's gesture. Peer delivery
  acceptance means transport acceptance, not approval, completion, or an answer.
  Blocked peers wait for the native idle boundary to avoid completing a subsequent
  turn accidentally. Errors, shutdown, transport loss and close also drain those
  receipts into the same peer policy; reconnect must never silently discard them.
  Window shutdown retains auto-policy receipts as queued without starting another turn.

## Compatibility and limitations

The session-management RPC and pre-prompt policy hook are experimental upstream
surfaces. They were verified against an installed CLI, not inferred from session files.
Startup fails visibly if the selected CLI does not implement them: do not fall back to
unrestricted native peer execution. The session becomes addressable after its first
prompt and remains addressable while the window's session is connected.

The existing fleet tools remain available for fleet recipients; Copilot recipients
use their distinct tools. A raw provider peer is never upgraded to a verified fleet
sender. Provider token counts are reported when supplied; dollar costs remain unknown
when the subscription provider does not supply them.

## References

- GitHub Copilot SDK: https://github.com/github/copilot-sdk
- Compatibility: https://docs.github.com/en/copilot/how-tos/copilot-sdk/troubleshooting/compatibility
- Installed CLI documentation: `copilot help config`, `copilot help sessions`.

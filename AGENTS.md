nanobot-soulboard is a UI for nanobot providing a web UI and switching between multiple agent souls.

Dev guide:

- Backend will be in python, same as nanobot.
- There will be multiple agents and workspaces but share the same `MessageBus` and `SessionManager`, etc. Only one agent can run at a time. To fully control the context, we need to inherit `ContextBuilder`.
- The web UI should be able to list souls, switch between them, stop or start a soul. For a started soul, we should be able to list the tools it gets attached to, and add or remove. This could be impossible in upstream, so maybe we need to destroy the agent loop object and create a new one if necessary. Also show other stats like model, attached channels, etc. Sessions should also be accessible on the web UI.
- Directory structure should still be under `~/.nanobot` but the hardcoded `workspace` will be dynamic.

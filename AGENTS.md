nanobot-soulboard is a platform for nanobot providing a web UI and switching between multiple agent souls.

Dev guide:

- Backend will be in python, same as nanobot. One python process runs all souls to avoid communication gaps.
- There will be multiple agents and workspaces.
- Do not touch upstream `nanobot` core files unless we need to contribute to it. For our downstream, the correct way is to subclass upstream's classes and override only the parts we need to change.
- Avoid `getattr` at any cost because it makes the codebase extremely hard to maintain.
- Frontend uses pnpm.
- Do not `from __future__ import annotations` because it is deprecated.

---
name: fleet
description: The session roster (/foundry:fleet) — native `/list-agents` annotated with each row's foundry programme, over the NATIVE session/container list. Read-only/advisory. Trigger to see all your parallel sessions and containers at a glance with their foundry context, or when supervising many sessions and asking "what is each one doing".
---

# /foundry:fleet

Run `/list-agents` — the native enumerator (this machine's sessions, plus container sessions
while both sides are on Remote Control). Foundry adds ONE overlay: for a row whose name matches a
known container/session name, annotate it with that workspace's programme — `next_action` read
from `.foundry/releases/<programme>/state.yaml`. Nothing else; the prior registry-based roster
steps are retired here (the fleet scripts stay until R4 removes them).

Read-only, never acts. For the cross-session message-kind convention (`FINDING` /
`NEEDS-INTERFACE` / `CHALLENGE` / `HANDOFF`), see `docs/how-to/deck-and-containers.md` and
`scripts/foundry_message_kind.py`.

---
name: architecture-recovery
description: >
  Recover safely when architecture context is stale, invalid, missing, or contradicted by source evidence;
  inspect last-good context and architecture deltas without treating them as current truth. Do not use for
  an ordinary fresh architecture lookup.
license: MIT
---

# Architecture recovery

Start with `archctx status` when `.archctx/architecture.json` exists (or the exact configured command from local guidance). A `STALE`, `INVALID`, or missing index is a warning,
not a prompt to guess architecture.

- Verify the cited canonical source directly before relying on retained context.
- If the selected install, config, or state directory is ambiguous, use `status --diagnose`
  once with the same config/state arguments. Keep its local paths private; it performs no
  refresh or writes. Older CLIs may not support it; use targeted local inspection then.
- Use `history`, `changed-since`, or `drift` when the installed version exposes them and the question is
  about a prior architecture state.
- Run `refresh` only when local index mutation is authorized and its validations can run. A failed refresh
  must leave last-good intact and remain labelled stale.

If recovery cannot establish fresh source evidence, continue with normal targeted discovery and state the
limitation plainly. Never turn recovery into an approval gate.

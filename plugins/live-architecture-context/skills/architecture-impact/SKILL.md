---
name: architecture-impact
description: >
  Assess which canonical architecture components a proposed edit or current diff may affect. Use before
  a cross-module or boundary-changing edit, or when asked what could break. Do not use for an obvious
  local edit confined to one known implementation.
license: MIT
---

# Architecture impact

Use the repository's Archctx command only when the change may cross an architecture boundary.

1. Check `status`; if it is `FRESH`, run `impact --files <exact changed paths>`.
2. Use the returned component IDs to decide which owners and source evidence to inspect. An empty result
   is a reason to continue normal targeted review, not a proof of no impact.
3. Let the repository watcher refresh after an edit. Run `refresh` yourself only when the task authorizes
   a local index update and no watcher is active.

Archctx narrows review; it neither approves a change nor replaces tests, source review, or release rules.

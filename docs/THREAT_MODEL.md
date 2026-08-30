# Threat model

Architecture context is a rebuildable index, never authority over source.

- Configured command argv and gates execute with the caller's permissions;
  review config changes as code and do not use untrusted configs.
- Evidence stores paths, snippets requested by config, line numbers and file
  hashes. Keep private configs/state out of public repositories.
- Failed validation never overwrites `last-good.json`; stale results are marked
  and instruct the agent to refresh, rather than guessing.
- This tool does not authenticate users, sandbox external tools, or prove
  runtime reachability. Use repository/CI policy for those boundaries.

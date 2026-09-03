# Compatibility

Config and retained records are version 1. A newer config is rejected rather
than guessed. Python 3.10+ is supported on Windows and Linux. The coordinator
uses only the standard library.

CALM and Archify are external command contracts, not embedded dependencies:
pin their command/version in each private repo configuration and promote only
when its deterministic gate passes. A CALM daemon with no configured
incremental command is labelled `external_daemon_unverified`.

`archctx-calm-query` is an optional stdlib-only client for an explicitly
managed loopback CALM Streamable HTTP endpoint. It never starts or supervises
CALM, and it returns no code facts unless CALM reports a ready, fresh watcher.
It cannot turn CALM's current status surface into an incremental receipt:
CALM does not publish the transaction's processed-path digest.

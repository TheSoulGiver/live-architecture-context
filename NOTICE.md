# Notice

This repository contains original coordinator code only. It does not vendor or
copy code from CALM or Archify. Users install and operate those MIT-licensed
projects independently; their versions and licenses remain their own.

CALM: https://github.com/Eilodon/CALM

Archify: https://github.com/tt-a1i/archify

Understand Anything: https://github.com/Egonex-AI/Understand-Anything

The live development map's layered focus and layout-preserving change overlay
were informed by Understand Anything at
`5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc` (MIT; copyright 2026 Yuxiang Lin and
Infinite Universe, Inc.). No upstream source is copied or vendored.

The optional `archctx-understand` adapter now also invokes that pinned
upstream's actual scanner, import resolver, Tree-sitter extraction, graph merge,
schema and fingerprints. Its file/architecture/tour agent instructions are
executed by the user's existing authorized coding agent, not by a new model
service. The separately installed checkout retains upstream's MIT license and
copyright notice. Normal LAC queries and the viewer do not require its Node
dependencies or execute a model. See `docs/UNDERSTAND.md` for the exact boundary.

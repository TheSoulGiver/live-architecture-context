# Release candidate checklist

1. Run `python -m unittest -v` and the synthetic demo commands.
2. Run `git diff --check`; inspect `git status --short` for private state.
3. Confirm README examples work from a clean clone on Windows and Linux CI.
4. Pin CALM/Archify commands in consumer configs; do not claim their outputs
   are authored facts.
5. Create a signed/tagged `v0.1.0-rc1` only after the above passes.

---
name: PULL_REQUEST_TEMPLATE
about: Pull request
title: ''
labels: ''
assignees: ''

---

## What does this change do, and why?

<!-- Plain language, not just "fixes bug" — what was actually wrong/missing,
     and how this addresses it. See CONTRIBUTING.md if you haven't yet. -->

## Checklist

- [ ] I explained the *why*, not just the *what*, above
- [ ] Docstrings/comments explain non-obvious tradeoffs, matching the project's existing style
- [ ] Tests added for any non-trivial new logic (see `tests/` for the style)
- [ ] `pytest tests/ -v` passes locally
- [ ] No hardcoded personal preferences (app lists, commands, colors) — config-driven instead
- [ ] No silent fallbacks — missing/malformed input fails loudly, not quietly

## Anything you want feedback on specifically?

<!-- Optional — e.g. "not sure if this belongs in the provider or the module",
     "open to a different approach here", etc. -->

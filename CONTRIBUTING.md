# Contributing to tuicc

Thank you for considering it!! This is an early, personal project, and I want to be upfront about how I review things, so contributing goes smoothly for both of us.

## How I review PRs

To responsibly evaluate and merge a change, I need to actually understand what it does and why — not just trust that it works. So:

- **Explain what your change does and why**, in the PR description, in plain language — not just "fixes bug", but what was actually wrong and how your fix addresses it. If I can't follow the reasoning, I can't responsibly merge it, no matter how correct the code is.
- **Match the project's existing commenting style.** Docstrings here explain *why* a decision was made, not just what a function does — see almost any file in `src/tuicc/` for the pattern. If your change introduces a non-obvious tradeoff, write it down the same way.
- **Tests for anything non-trivial.** If you add real logic (not just wiring), add a test for it — see `tests/` for the style (pure functions tested directly, fixtures instead of a live WM connection where possible). Tests are how I verify behavior I can't always confirm just by reading the diff.
- I might ask you to break a large PR into smaller ones, or to walk me through a piece of it in more detail. That's not a rejection — it's how I keep myself able to actually own and maintain this codebase long-term. Thanks for your patience with that.

## Ground rules the codebase already follows

These aren't arbitrary — they came out of real bugs this project already hit. Keep new code consistent with them:

- **No silent fallbacks.** A missing or malformed config value should fail loudly and clearly at startup, never quietly default to something the user didn't ask for. See `config.py`'s validation for the pattern.
- **No hardcoded personal preferences.** Anything that's specific to one person's setup (app lists, commands, colors) belongs in config, not in code.
- **Providers only know their WM; nothing else does.** If you're touching a provider, the `Provider` contract in `providers/base.py` is the only surface the rest of the codebase should ever need — see [Writing a WM Provider](https://github.com/Lshika-linux/tuicc/wiki/Writing-a-WM-Provider) on the wiki.
- **Modules own their own draw + nav_items; the core never guesses.** See [Writing a Module](https://github.com/Lshika-linux/tuicc/wiki/Writing-a-Module) for the contract.

## Before you start something big

If you're thinking about a new provider (especially for a scrollable WM like `scroll`/niri) or a substantial new module, open an issue first and let's talk through the shape of it together. I'd rather figure out the right design with you up front than review a large PR I have to ask you to significantly rework.

Smaller fixes, docs corrections, and self-contained modules are welcome as PRs directly — no need to ask first.

## Running things locally

```bash
git clone https://github.com/Lshika-linux/tuicc
cd tuicc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

```bash
nix-shell -p 'python3.withPackages (ps: [ps.pytest ps.i3ipc ps.jeepney])' --run 'PYTHONPATH=src pytest tests/ -v'
```

See the [README](README.md) and the [wiki](https://github.com/Lshika-linux/tuicc/wiki) for architecture details.

## Not sure where to start?

Check open issues, or the README's "Not yet built" section, or just ask — open an issue with what you're interested in and I'll point you somewhere useful.

One more time, thank you for considering helping this project. That means a lot to me.

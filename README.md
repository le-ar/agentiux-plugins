# agentiux-plugins

Source-of-truth repository for home-local Codex plugins, release gates, and supporting tooling.

## Repository Layout

- `plugins/agentiux-dev`: external-state workflow kernel for Codex-driven development, deterministic verification, design orchestration, repo-aware Git helpers, and the local dashboard.
- `.github/workflows/release-readiness.yml`: CI entrypoint for the production gate.
- `README.md`: repo-level development and release workflow.

## Quick Start

Run the production gate from the repo root:

```bash
python3 plugins/agentiux-dev/scripts/release_readiness.py run --repo-root . --smoke-runs 1
```

Sync the plugin into the home-local install root:

```bash
python3 plugins/agentiux-dev/scripts/install_home_local.py
```

Launch the local dashboard:

```bash
python3 plugins/agentiux-dev/scripts/agentiux_dev_gui.py launch
```

Run the standalone smoke suite:

```bash
python3 plugins/agentiux-dev/scripts/smoke_test.py
```

## Operating Model

- Edit plugin source in this repository.
- Sync a tested snapshot into `~/plugins/agentiux-dev` when you want to use it as the installed runtime copy.
- Keep Codex-only workspace state under `~/.agentiux/agentiux-dev/`.
- Treat the source repo and installed copy as separate roots: source is for development, installed copy is for daily use.

## Stable Use And Development

1. Develop and verify changes in this repository.
2. Run the readiness gate until it passes.
3. Sync the passing snapshot with `python3 plugins/agentiux-dev/scripts/install_home_local.py`.
4. Use the installed copy in Codex while continuing development here until the next sync.

## Release Gate

The default production gate for this repository is `plugins/agentiux-dev/scripts/release_readiness.py`. It is intended to stay identical locally and in GitHub Actions so the local pass/fail signal matches CI.

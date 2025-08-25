# Repository Guidelines

This guide explains how to work on AI Radio with consistent structure, build steps, and review practices.

## Project Structure & Module Organization
- `ui/`: Flask app and Socket.IO backend (`app.py`, `routes/`, `services/`, `utils/`, `tests/`).
- `radio-frontend/`: React + TypeScript source (built assets are deployed into `ui/`).
- `radio.liq`: Liquidsoap configuration for streaming.
- `tts/`, `voices/`, `tts_queue/`: Generated TTS audio and voice assets.
- `cache/`, `logs/`: Runtime caches and logs.
- Key data/config: `ai_radio.db`, `dj_settings.json`, `library_clean.m3u`.
- Top-level tests and scripts: `test_radio_liq.py`, `test_radio_functional.sh`.

## Build, Test, and Development Commands
- Backend: `python ui/app.py` (serves on `config.PORT`, default 5055).
- Liquidsoap: `liquidsoap radio.liq` (starts telnet/Harbor/stream endpoints).
- Frontend build: `cd radio-frontend && npm ci && npm run build && cp -R build/* ../ui/`.
- UI tests: `python ui/tests/run_tests.py` or a module: `python ui/tests/run_tests.py ui/tests/test_routes.py`.
- Liquidsoap config tests: `python test_radio_liq.py` (checks syntax/ports/files).
- Functional smoke tests: `bash test_radio_functional.sh` (requires services running).

## Coding Style & Naming Conventions
- Python: 4-space indent, type hints, snake_case for functions/vars, PascalCase for classes, module-level docstrings. Keep logic in `services/`, HTTP in `routes/`.
- React/TS: PascalCase components, camelCase functions, colocate UI logic in `radio-frontend/src`. CRA ESLint is enabled; prefer Prettier defaults.
- Shell: set safe flags, emit clear logs, avoid hard-coded paths where possible.

## Testing Guidelines
- Python tests live in `ui/tests/` and follow `test_*.py`. Use `run_tests.py` to run all. Focus on `services/` and `routes/` with mocks for I/O.
- Frontend: `npm test` in `radio-frontend/` (Jest + RTL).
- Add tests for new endpoints, DB logic, and Liquidsoap interactions where feasible.

## Commit & Pull Request Guidelines
- Messages: imperative, concise, scope-first when useful. Example: `Fix time remaining calculation` or `feat(ui): modernize routes`. Emojis are acceptable and used sparingly.
- PRs: include summary, linked issue, before/after screenshots for UI, test plan, and notes on config/migrations. Update README and this file when behavior changes.

## Security & Configuration Tips
- Do not commit secrets. Use env vars (`ELEVENLABS_API_KEY`, etc.) and redact local paths.
- Keep `dj_settings.json` and `library_clean.m3u` minimal in PRs; avoid leaking private media paths.

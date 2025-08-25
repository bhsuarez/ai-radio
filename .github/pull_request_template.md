## Summary
- What does this PR change and why?

## Changes
- High-level bullets of key changes
- Note any migrations, config updates, or deprecations

## Tests
- How did you test? Include commands and coverage focus
- Backend: `python ui/tests/run_tests.py`
- Frontend: `cd radio-frontend && npm test -- --watchAll=false`
- Liquidsoap: `python test_radio_liq.py`
- Functional (optional): `bash test_radio_functional.sh`

## Screenshots / Recordings (UI)
- Before/After visuals if UI changes are included

## Deployment / Config
- New env vars or secrets (e.g., `ELEVENLABS_API_KEY`)
- Service restarts required (Liquidsoap/Flask)

## Checklist
- [ ] Includes tests or rationale for why not
- [ ] Updates docs (README/AGENTS.md) if behavior changed
- [ ] No secrets, tokens, or private file paths committed
- [ ] Backwards compatible or includes migration steps

## Linked Issues
- Closes #<id>

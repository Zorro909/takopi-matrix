# changelog

## v0.1.1 (2026-01-14)

### fixes

- Fix `AttributeError: 'TransportRuntime' object has no attribute 'project_key_for_alias'` in RoomProjectMap by using correct method `normalize_project_key()`

## v0.1.0 (2026-01-14)

Initial release of takopi-matrix.

### changes

- Matrix protocol support via matrix-nio
- End-to-end encryption (E2EE) by default
- Voice message transcription (OpenAI Whisper)
- File download support
- Interactive onboarding wizard
- Multi-room support with per-room engine defaults
- Project-to-room binding
- Room-specific engine routing
- GitHub workflows for CI and PyPI publishing
- Modular package structure (client/, bridge/, onboarding/)

### fixes

- Fix Pydantic v2 transports config handling in onboarding

### docs

- Add comprehensive documentation structure
- Add README with installation and configuration guide

### known issues

- E2EE encryption key exchange may fail in some scenarios. If you experience issues with encrypted rooms, try re-verifying the bot session.

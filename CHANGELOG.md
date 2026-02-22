# changelog

## v0.4.0 (2026-02-22)

### changes
- Add engine overrides, trigger mode, and split commands module
- Add built-in parity commands, file transfer, and repo onboarding
- Add chat/thread session auto-resume stores
- Harden built-ins and ship reload restart behavior
- Match Telegram voice transcription settings (configurable max_bytes)

### fixes
- Make reload restart graceful and reliable
- Make reply-resume robust for encrypted/edited events
- Align session continuity parity for commands and startup cwd
- Fix Matrix mentions reply detection and override type safety
- Resolve post-rebase type errors and document restart mechanism
- Add git URL validation and alias conflict check
- Fix broken `_OPENAI_AUDIO_MAX_BYTES` test import (constant removed in favor of configurable `max_bytes`)

## v0.3.0 (2026-02-18)

### changes
- feat(matrix): add SAS verify-device helper for E2EE setup. See https://github.com/Zorro909/takopi-matrix/pull/12

### fixes
- fix(e2ee): make Megolm key sharing reliable. See https://github.com/Zorro909/takopi-matrix/pull/11

## v0.2.0 (2026-01-16)

### changes

- Bump takopi dependency to 0.20.0
- Auto-join room invites from allowed users
- Refactor to use takopi.api module, own markdown/progress rendering

### fixes

- Fix DeviceStore.items() usage (replace non-existent .get() method)
- Resolve type errors with takopi.api imports

## v0.1.2 (2026-01-15)

### fixes

- Fix `asyncio.run()` nested event loop error in interactive setup wizard when running inside existing async context

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
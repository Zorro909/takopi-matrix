# Matrix Transport

The Matrix transport allows takopi to operate via Matrix homeservers (Element, Synapse, etc.).

## Installation

### Prerequisites

This package requires **libolm** (version 3.x) for end-to-end encryption:

| Platform | Command |
|----------|---------|
| Debian/Ubuntu | `sudo apt-get install libolm-dev` |
| Fedora | `sudo dnf install libolm-devel` |
| Arch Linux | `sudo pacman -S libolm` |
| openSUSE | `sudo zypper install libolm-devel` |
| macOS (Homebrew) | `brew install libolm` |

### Install

```bash
pip install takopi-matrix
```

E2EE (end-to-end encryption) support is included by default.

## Configuration

Add the following to `~/.takopi/takopi.toml`:

```toml
transport = "matrix"
default_engine = "claude"

[transports.matrix]
homeserver = "https://matrix.example.org"
user_id = "@bot:example.org"
access_token = "syt_your_access_token_here"
# OR use password authentication:
# password = "your_password"

# Required: list of room IDs to monitor
room_ids = ["!abc123:example.org", "!def456:example.org"]

# Optional: device ID (auto-generated if not set)
device_id = "TAKOPI"

# Optional: users allowed to auto-accept room invites from
user_allowlist = ["@admin:example.org"]

# Optional: voice transcription via OpenAI
voice_transcription = false
voice_max_bytes = 10485760 # 10 MB
voice_transcription_model = "gpt-4o-mini-transcribe" # optional
voice_transcription_base_url = "http://localhost:8000/v1" # optional
voice_transcription_api_key = "local" # optional

# Optional: file download settings
file_download = true
file_download_max_mb = 50

# Optional: E2EE settings
e2ee_enabled = true
crypto_store_path = "~/.takopi/matrix_crypto.db"
```

## Interactive Setup

Run the interactive setup wizard:

```bash
takopi --onboard
```

This will guide you through:
1. Homeserver discovery (via `.well-known`)
2. Authentication (token or password)
3. Room selection
4. Agent selection

## Features

### Message Formatting

The Matrix transport uses full Matrix HTML formatting (`org.matrix.custom.html`) for rich message display:
- Code blocks with syntax highlighting
- Bold, italic, and other markdown formatting
- Proper list rendering

### Voice Transcription

Like the Telegram transport, Matrix supports voice message transcription via OpenAI:

1. Set `voice_transcription = true` in config
2. Set `OPENAI_API_KEY` environment variable (or `voice_transcription_api_key` in config)
3. Optionally set `voice_transcription_base_url` for a local OpenAI-compatible server
4. Send voice messages (m.audio) to trigger transcription

If your local server requires a specific model name, set `voice_transcription_model`
(for example, `whisper-1`).

### File Downloads

When users send files (images, documents), the Matrix transport:
1. Downloads the file to the current working directory
2. Adds an `@FILENAME` reference to the message text
3. Uses content-hash suffixes to avoid filename collisions

Example: If a user sends `diagram.png`, the prompt becomes:
```
@diagram_a3f2b1.png

Can you analyze this diagram?
```

### Typing Indicators

The bot shows typing indicators (`m.typing`) while processing requests.

### Read Receipts

The bot sends read receipts after processing each message.

### Cancel Support

Two ways to cancel a running operation:
1. Send `/cancel` as a reply to the progress message
2. React to the progress message with ❌

### E2EE (End-to-End Encryption)

E2EE is enabled by default (requires libolm):
- Crypto state is stored in SQLite at `crypto_store_path`
- Encrypted rooms are automatically detected and handled
- Device verification via SAS/emoji is supported

#### Verify The Takopi Device (SAS/Emoji)

If your rooms use “only share keys to verified devices”, you must verify the Takopi
session once so other clients will share Megolm keys to it.

Run the verifier on the host/container where Takopi runs (stop Takopi first):

```sh
takopi-matrix verify-device --config ~/.takopi/takopi.toml --allow @you:example.org
```

Then in your Matrix client (Element/FluffyChat), verify the Takopi session and
confirm the SAS emojis/decimals shown by the verifier.

Initiator mode is available but less reliable across clients:

```sh
takopi-matrix verify-device --initiate-to @you:example.org
```

Useful initiator options for flaky client popups:

```sh
takopi-matrix verify-device \
  --initiate-to @you:example.org \
  --initiate-device-id YOURDEVICEID \
  --initiate-retries 3 \
  --initiate-retry-interval 10 \
  --no-send-encrypted
```

### Auto-Join Invites

When `user_allowlist` is configured, the bot will automatically accept room invites from listed users:

```toml
user_allowlist = ["@admin:example.org", "@trusted:example.org"]
```

Invites from unlisted users are ignored.

### Trigger Mode (v0.2+)

Control when the bot responds to messages in a room:

- **all** (default): Bot responds to every message
- **mentions**: Bot only responds when mentioned by display name or user ID

The trigger mode is configured per-room via slash command (when available) or programmatically via `RoomPrefsStore`.

When in mentions mode, the bot also responds to:
- Direct replies to the bot's messages
- Replies to in-progress task messages

### Engine Overrides (v0.2+)

Override model and reasoning settings per-room:

- **model**: Override which model the engine uses (e.g., `gpt-4-turbo`, `claude-3-opus`)
- **reasoning**: Set reasoning level (`low`, `medium`, `high`)

Overrides are resolved with priority: thread → room → default config.

### Room Preferences

Per-room settings are persisted to `~/.takopi/matrix_prefs.json`:
- Default engine per room
- Trigger mode
- Engine overrides (model, reasoning)

Settings survive bot restarts and support hot-reload (changes take effect without restart).

## Room ID Format

Room IDs must be in the format `!roomid:server`. You can find your room ID in Element under Room Settings → Advanced.

Room aliases (like `#room:server`) are resolved during interactive setup.

## Differences from Telegram

| Feature | Telegram | Matrix |
|---------|----------|--------|
| IDs | Numeric | String (event IDs) |
| Formatting | Entities | HTML |
| Message Edits | API call | m.replace event |
| Deletion | API call | m.room.redaction |
| Threading | reply_to_message_id | Reply in room |
| E2EE | N/A | Optional |
| Rate Limits | Hard per-chat | Server retry_after_ms |

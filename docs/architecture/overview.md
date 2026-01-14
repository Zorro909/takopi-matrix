# Architecture Overview

## Component Diagram

```
                    +-------------------+
                    |    backend.py     |
                    | (Entry point,     |
                    |  configuration,   |
                    |  lifecycle)       |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
    +---------v-------+  +---v---------+  +-v-----------+
    |   onboarding/   |  |   bridge/   |  |   client/   |
    | (Setup wizard,  |  | (Transport, |  | (Matrix     |
    |  validation)    |  |  commands,  |  |  protocol,  |
    +-----------------+  |  events)    |  |  rate limit)|
                         +------+------+  +------+------+
                                |                |
                                +-------+--------+
                                        |
                                +-------v--------+
                                |   matrix-nio   |
                                | (Matrix SDK)   |
                                +----------------+
```

## Layer Responsibilities

### backend.py
- Entry point for Matrix transport registration
- Configuration loading and validation
- Bot lifecycle management (start/stop)
- Integrates with takopi's transport system

### bridge/ Package
- Message routing between Matrix and takopi
- Command dispatch (slash commands like /help, /cancel)
- Event processing pipeline (sync, timeline, reactions)
- Voice transcription integration (OpenAI Whisper)
- Cancel handling (commands and reactions)

### client/ Package
- Matrix protocol operations (login, sync, send, edit, redact)
- Rate limiting via outbox pattern
- E2EE management (key upload, device trust, decryption)
- Event parsing (messages, media, audio, reactions)

### onboarding/ Package
- Interactive setup wizard
- Homeserver discovery
- Credential validation
- Configuration file generation

## Package Structure

```
src/takopi_matrix/
+-- __init__.py              # Package exports
+-- types.py                 # Data classes (MatrixIncomingMessage, etc.)
+-- render.py                # Markdown rendering for Matrix
+-- state_store.py           # JSON state persistence
+-- room_prefs.py            # Per-room preferences
+-- room_projects.py         # Room-project mapping
+-- files.py                 # File download utilities
+-- engine_defaults.py       # Engine resolution logic
+-- backend.py               # Transport registration
+-- crypto.py                # E2EE management
|
+-- client/                  # Matrix client package
|   +-- __init__.py          # Re-exports
|   +-- protocol.py          # Type definitions, exceptions
|   +-- outbox.py            # Rate-limited operation queue
|   +-- parsers.py           # Event parsing functions
|   +-- content_builders.py  # Message content formatters
|   +-- client.py            # MatrixClient class
|
+-- bridge/                  # Bridge package (TODO)
|   +-- ...
|
+-- onboarding/              # Onboarding package (TODO)
    +-- ...
```

## Data Flow

### Startup Sequence

1. `backend.py` registers Matrix transport with takopi
2. Configuration loaded via `onboarding/validation.py`
3. `MatrixClient` initialized from `client/client.py`
4. E2EE initialized if libolm available
5. `MatrixTransport` created from `bridge/transport.py`
6. Main sync loop started via `bridge/runtime.py`

### Message Processing Flow

```
Matrix Server
     |
     v
+----+----+
| nio SDK | (sync response)
+---------+
     |
     v
+---------+
| bridge/ |
| runtime | (sync loop)
+---------+
     |
     v
+---------+
| bridge/ |
| events  | (process timeline)
+---------+
     |
     +--------+--------+
     |        |        |
     v        v        v
+--------+ +--------+ +--------+
| client/| | bridge/| | bridge/|
| parsers| | commands| | cancel |
+--------+ +--------+ +--------+
     |        |        |
     v        v        v
+----------------------------+
| MatrixIncomingMessage      |
+----------------------------+
     |
     v
+----------------------------+
| takopi engine execution    |
+----------------------------+
     |
     v
+----------------------------+
| bridge/transport.py        |
| (send response)            |
+----------------------------+
     |
     v
+----------------------------+
| client/client.py           |
| (MatrixClient.send_message)|
+----------------------------+
     |
     v
Matrix Server
```

### Message Sending Flow

1. `bridge/transport.py` calls `MatrixClient.send_message()`
2. `client/content_builders.py` formats content (reply/edit)
3. `client/outbox.py` queues operation with priority
4. `client/client.py` executes via nio client
5. Rate limiting applied between operations

## Key Abstractions

### MatrixClient
Core Matrix operations with rate limiting. All API calls go through an outbox that enforces minimum intervals between requests to avoid rate limiting.

### MatrixTransport
Implements takopi's `Transport` interface. Handles:
- Sending messages (plain, formatted, replies)
- Editing messages (streaming updates)
- Message deletion
- Typing indicators

### MatrixPresenter
UI state updates during message processing. Tracks:
- Typing indicators
- Progress messages
- Error states

### MatrixBridgeConfig
Configuration for the Matrix bridge:
- Room IDs
- Voice transcription settings
- File download settings
- Engine defaults

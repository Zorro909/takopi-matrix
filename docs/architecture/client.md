# Client Package

The `client/` package handles all Matrix protocol operations with built-in rate limiting.

## Modules

### protocol.py

Type definitions and exceptions for Matrix operations.

**Priority Constants:**
- `SEND_PRIORITY = 0` - Highest priority for new messages
- `DELETE_PRIORITY = 1` - Redaction operations
- `EDIT_PRIORITY = 2` - Message edits (lower priority, coalesced)
- `TYPING_PRIORITY = 3` - Lowest priority, non-critical

**Exceptions:**
- `RetryAfter` - Base exception for retry delays
- `MatrixRetryAfter` - Matrix-specific rate limit exception

**Protocol:**
- `NioClientProtocol` - Structural typing for nio.AsyncClient

### outbox.py

Rate-limited operation queue using the outbox pattern.

**Classes:**
- `OutboxOp` - A queued operation with priority, timing, and result
- `MatrixOutbox` - Queue that enforces minimum intervals between operations

**Features:**
- Priority-based ordering (lower number = higher priority)
- Automatic retry on rate limit responses
- Operation coalescing (new edit replaces pending edit)
- Graceful shutdown with pending operation cleanup

### parsers.py

Event parsing functions that convert nio events to domain types.

**Functions:**
- `parse_matrix_error(response)` - Extract errcode and retry_after
- `parse_room_message(event, room_id, ...)` - Text messages
- `parse_room_media(event, room_id, ...)` - Images, files
- `parse_room_audio(event, room_id, ...)` - Voice messages
- `parse_reaction(event, room_id, ...)` - Emoji reactions

All parsers:
- Filter by allowed room IDs
- Filter out own messages
- Extract reply-to information
- Handle both regular and encrypted events

### content_builders.py

Message content formatters for Matrix message types.

**Functions:**
- `_build_reply_content(body, formatted_body, reply_to_event_id)` - Reply threading
- `_build_edit_content(body, formatted_body, original_event_id)` - Message edits

### client.py

Main `MatrixClient` class with all Matrix operations.

**Key Methods:**

```python
# Authentication
async def login() -> bool
async def init_e2ee() -> bool

# Sync
async def sync(timeout_ms, full_state) -> SyncResponse | None

# Messaging
async def send_message(room_id, body, formatted_body, reply_to_event_id) -> dict | None
async def edit_message(room_id, event_id, body, formatted_body, wait) -> dict | None
async def redact_message(room_id, event_id, reason) -> bool

# Status
async def send_typing(room_id, typing, timeout_ms) -> bool
async def send_read_receipt(room_id, event_id) -> bool

# Media
async def download_file(mxc_url, max_size, file_info) -> bytes | None

# E2EE
async def ensure_room_keys(room_id) -> None
async def trust_room_devices(room_id) -> None
async def decrypt_event(event) -> Event | None

# Lifecycle
async def close() -> None
```

## Usage Examples

### Basic Message Sending

```python
from takopi_matrix.client import MatrixClient

client = MatrixClient(
    homeserver="https://matrix.example.org",
    user_id="@bot:example.org",
    access_token="syt_...",
)

await client.login()

# Send a simple message
result = await client.send_message(
    room_id="!room:example.org",
    body="Hello, Matrix!",
)

# Send a reply
result = await client.send_message(
    room_id="!room:example.org",
    body="This is a reply",
    reply_to_event_id="$original_event_id",
)

# Edit a message
result = await client.edit_message(
    room_id="!room:example.org",
    event_id="$event_to_edit",
    body="Updated content",
)

await client.close()
```

### Parsing Incoming Events

```python
from takopi_matrix.client import parse_room_message, parse_reaction

# In your sync handler:
for event in room_events:
    if isinstance(event, nio.RoomMessageText):
        msg = parse_room_message(
            event,
            room_id,
            allowed_room_ids={"!room:example.org"},
            own_user_id="@bot:example.org",
        )
        if msg:
            # Process the message
            print(f"From {msg.sender}: {msg.text}")

    elif isinstance(event, nio.ReactionEvent):
        reaction = parse_reaction(
            event,
            room_id,
            allowed_room_ids={"!room:example.org"},
            own_user_id="@bot:example.org",
        )
        if reaction:
            # Handle reaction
            print(f"{reaction.sender} reacted with {reaction.key}")
```

### E2EE Operations

```python
# Initialize E2EE after login
if client.e2ee_available:
    await client.init_e2ee()

# Trust all devices in a room (auto-trust mode)
await client.trust_room_devices(room_id)

# Ensure encryption keys are shared
await client.ensure_room_keys(room_id)

# Decrypt an encrypted event
if isinstance(event, nio.MegolmEvent):
    decrypted = await client.decrypt_event(event)
    if decrypted:
        # Process decrypted event
        ...
```

## Rate Limiting

The outbox automatically handles rate limiting:

1. All operations go through `enqueue_op()`
2. Operations are sorted by priority, then by queue time
3. Minimum interval (default 100ms) enforced between operations
4. On `M_LIMIT_EXCEEDED`, operation is requeued with delay

Edit operations use a special key `("edit", room_id, event_id)` so newer edits replace pending ones (only the final content matters).

## Error Handling

Most methods return `None` or `False` on failure and log errors. Exceptions are only raised for rate limiting (`MatrixRetryAfter`) which the outbox handles internally.

```python
result = await client.send_message(room_id, body)
if result is None:
    # Message failed - check logs for details
    ...
else:
    event_id = result["event_id"]
    # Message sent successfully
```

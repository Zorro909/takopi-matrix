# Testing Guide

## Test Structure

```
tests/
+-- conftest.py               # Shared fixtures
+-- matrix_fixtures.py        # Matrix-specific test helpers
+-- test_matrix_client.py     # Client package tests
+-- test_matrix_bridge.py     # Bridge tests
+-- test_matrix_files.py      # File handling tests
+-- test_matrix_render.py     # Markdown rendering tests
+-- test_matrix_types.py      # Data type tests
+-- test_matrix_crypto.py     # E2EE tests
+-- test_matrix_room_prefs.py # Room preferences tests
+-- test_matrix_room_projects.py # Room-project mapping tests
+-- test_matrix_engine_defaults.py # Engine resolution tests
+-- test_matrix_state_store.py    # State persistence tests
+-- test_matrix_onboarding.py     # Onboarding wizard tests
```

## Running Tests

### All Tests

```bash
uv run pytest tests/ -v
```

### Specific Test File

```bash
uv run pytest tests/test_matrix_client.py -v
```

### Specific Test Class

```bash
uv run pytest tests/test_matrix_client.py::TestE2EEAutoTrust -v
```

### Specific Test Function

```bash
uv run pytest tests/test_matrix_client.py::test_parse_room_message_basic -v
```

### Pattern Matching

```bash
# Tests containing "parse"
uv run pytest -k "parse" -v

# Tests containing "e2ee" or "crypto"
uv run pytest -k "e2ee or crypto" -v
```

### With Coverage

```bash
uv run pytest tests/ --cov=takopi_matrix --cov-report=html
open htmlcov/index.html
```

## Test Fixtures

### matrix_fixtures.py

Common test data and helpers:

```python
from matrix_fixtures import (
    MATRIX_HOMESERVER,    # "https://matrix.example.org"
    MATRIX_USER_ID,       # "@testbot:example.org"
    MATRIX_ROOM_ID,       # "!testroom:example.org"
    MATRIX_EVENT_ID,      # "$testevent123"
    MATRIX_SENDER,        # "@alice:example.org"
    make_matrix_message,  # Create MatrixIncomingMessage
    make_matrix_reaction, # Create MatrixReaction
    make_nio_event,       # Create nio.RoomMessageText mock
    make_sync_response,   # Create nio.SyncResponse mock
)
```

### conftest.py

Pytest fixtures:

```python
@pytest.fixture
def matrix_client():
    """Create a test MatrixClient."""
    return MatrixClient(
        homeserver=MATRIX_HOMESERVER,
        user_id=MATRIX_USER_ID,
        access_token="test_token",
    )

@pytest.fixture
def mock_nio_client():
    """Create a mock nio.AsyncClient."""
    ...
```

## Writing Tests

### Unit Test Example

```python
import pytest
from takopi_matrix.client import parse_room_message

def test_parse_room_message_basic():
    """Test parsing a simple text message."""
    event = make_nio_event(
        sender="@alice:example.org",
        body="Hello, world!",
    )

    result = parse_room_message(
        event,
        room_id="!room:example.org",
        allowed_room_ids={"!room:example.org"},
        own_user_id="@bot:example.org",
    )

    assert result is not None
    assert result.sender == "@alice:example.org"
    assert result.text == "Hello, world!"
```

### Async Test Example

```python
import pytest
import anyio

@pytest.mark.anyio
async def test_matrix_client_login():
    """Test client login with token."""
    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="test_token",
    )

    # Mock the nio client
    with patch.object(client, "_ensure_nio_client") as mock:
        mock.return_value = MagicMock()

        result = await client.login()

        assert result is True
        assert client._logged_in is True
```

### Mocking nio Client

```python
from unittest.mock import MagicMock, AsyncMock

def test_with_mocked_nio():
    """Test with mocked nio responses."""
    mock_nio = MagicMock()
    mock_nio.room_send = AsyncMock(return_value=nio.RoomSendResponse(
        room_id="!room:example.org",
        event_id="$new_event",
    ))

    # Inject mock
    client._nio_client = mock_nio

    # Test
    result = await client.send_message(
        room_id="!room:example.org",
        body="Test message",
    )

    assert result["event_id"] == "$new_event"
    mock_nio.room_send.assert_called_once()
```

## Test Categories

### Parser Tests

Test that nio events are correctly converted to domain types:

```python
def test_parse_room_message_filters_own():
    """Own messages should be filtered out."""
    event = make_nio_event(sender="@bot:example.org")

    result = parse_room_message(
        event,
        room_id="!room:example.org",
        allowed_room_ids={"!room:example.org"},
        own_user_id="@bot:example.org",  # Same as sender
    )

    assert result is None
```

### Outbox Tests

Test rate limiting and operation queuing:

```python
@pytest.mark.anyio
async def test_outbox_priority_ordering():
    """Higher priority operations execute first."""
    outbox = MatrixOutbox()
    results = []

    await outbox.enqueue(
        key="low",
        op=OutboxOp(
            execute=lambda: results.append("low"),
            priority=TYPING_PRIORITY,
            ...
        ),
    )
    await outbox.enqueue(
        key="high",
        op=OutboxOp(
            execute=lambda: results.append("high"),
            priority=SEND_PRIORITY,
            ...
        ),
    )

    # High priority should execute first
    assert results == ["high", "low"]
```

### E2EE Tests

Test encryption operations:

```python
@pytest.mark.anyio
async def test_trust_room_devices():
    """Test auto-trusting devices in a room."""
    client = MatrixClient(...)

    # Mock the device store
    mock_device = MagicMock(verified=False)
    mock_nio = MagicMock()
    mock_nio.rooms = {"!room:example.org": MagicMock(users={"@alice:example.org": {}})}
    mock_nio.device_store = {"@alice:example.org": {"DEVICE1": mock_device}}

    client._nio_client = mock_nio

    await client.trust_room_devices("!room:example.org")

    mock_nio.verify_device.assert_called_once_with(mock_device)
```

## Debugging Failed Tests

### Verbose Output

```bash
uv run pytest tests/test_failing.py -v --tb=long
```

### Print Statements

```bash
uv run pytest tests/test_failing.py -v -s
```

### Drop into Debugger

```bash
uv run pytest tests/test_failing.py --pdb
```

### Run Only Failed Tests

```bash
uv run pytest tests/ --lf
```

## CI Integration

Tests run automatically on:
- Pull requests
- Pushes to main

The CI pipeline:
1. Checks formatting (`ruff format --check`)
2. Runs linter (`ruff check`)
3. Runs type checker (pyright)
4. Runs tests (`pytest`)

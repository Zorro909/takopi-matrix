# Development Setup

## Prerequisites

- **Python 3.14+** (uses modern typing features)
- **libolm** (for E2EE support)
- **uv** package manager (recommended)

### Installing libolm

**Fedora/RHEL:**
```bash
sudo dnf install libolm-devel
```

**Ubuntu/Debian:**
```bash
sudo apt install libolm-dev
```

**macOS:**
```bash
brew install libolm
```

**Arch Linux:**
```bash
sudo pacman -S libolm
```

## Installation

### Clone the Repository

```bash
git clone https://github.com/Zorro909/takopi-matrix.git
cd takopi-matrix
```

### Install Dependencies

Using uv (recommended):
```bash
uv sync
```

Or using pip:
```bash
pip install -e ".[dev]"
```

### Verify Installation

```bash
uv run python -c "from takopi_matrix import BACKEND; print('OK')"
```

## Running Tests

Run all tests:
```bash
uv run pytest tests/ -v
```

Run specific test file:
```bash
uv run pytest tests/test_matrix_client.py -v
```

Run tests matching pattern:
```bash
uv run pytest -k "test_parse" -v
```

## Code Quality

### Linting and Formatting

Check formatting:
```bash
just check
```

Auto-format:
```bash
just format
```

### Type Checking

The project uses pyright for type checking. Check types with:
```bash
just check
```

## Project Structure

```
takopi-matrix/
+-- src/takopi_matrix/     # Main package
|   +-- client/            # Matrix client (package)
|   +-- bridge.py          # Message routing (TODO: split)
|   +-- onboarding.py      # Setup wizard (TODO: split)
|   +-- ...
+-- tests/                 # Test suite
+-- docs/                  # Documentation
+-- pyproject.toml         # Project configuration
+-- Justfile               # Task runner commands
```

## Configuration

### Environment Variables

Create a `.env` file for local development:

```bash
# Matrix credentials
MATRIX_HOMESERVER=https://matrix.example.org
MATRIX_USER_ID=@bot:example.org
MATRIX_ACCESS_TOKEN=syt_...

# OpenAI (for voice transcription)
OPENAI_API_KEY=sk-...
```

### Configuration File

The bot uses TOML configuration. Run the onboarding wizard to generate:

```bash
takopi --onboard
```

This creates `~/.config/takopi/config.toml` with Matrix settings.

## Development Workflow

### Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/my-feature
   ```

2. Make changes and add tests

3. Run tests:
   ```bash
   uv run pytest tests/ -v
   ```

4. Check formatting:
   ```bash
   just check
   ```

5. Commit with descriptive message

### Running the Bot Locally

```bash
# With default configuration
takopi --transport matrix

# With specific config file
takopi --config path/to/config.toml --transport matrix
```

### Debugging

Enable debug logging:
```bash
TAKOPI_LOG_LEVEL=DEBUG takopi --transport matrix
```

Or in Python:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Common Issues

### libolm Not Found

If you get import errors for E2EE, ensure libolm is installed:
```bash
python -c "import nio; print(hasattr(nio, 'crypto'))"
```

Should print `True` if E2EE is available.

### Token Expired

If login fails, regenerate your access token via Element or your Matrix client.

### Rate Limiting

The bot has built-in rate limiting. If you see frequent `M_LIMIT_EXCEEDED` errors, increase the `interval` parameter in MatrixClient.

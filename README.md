# 🐙 takopi-matrix

Matrix transport backend for [takopi](https://github.com/banteg/takopi).

## Features

- Matrix protocol support via [matrix-nio](https://github.com/matrix-nio/matrix-nio)
- End-to-end encryption (E2EE) by default
- Voice message transcription (OpenAI Whisper)
- File download support
- Interactive onboarding wizard
- Multi-room support with per-room engine defaults
- Project-to-room binding

## Requirements

- Python ≥3.14
- [libolm](https://gitlab.matrix.org/matrix-org/olm) 3.x (for E2EE)
- takopi ≥0.18

## Installation

### 1. Install libolm

| Platform | Command |
|----------|---------|
| Debian/Ubuntu | `sudo apt-get install libolm-dev` |
| Fedora | `sudo dnf install libolm-devel` |
| Arch Linux | `sudo pacman -S libolm` |
| openSUSE | `sudo zypper install libolm-devel` |
| macOS (Homebrew) | `brew install libolm` |

### 2. Install takopi-matrix

```bash
pip install takopi-matrix
```

Or with uv:

```bash
uv tool install takopi --with takopi-matrix
```

## Configuration

### Interactive Setup

```bash
takopi --onboard
```

### Manual Configuration

Add to `~/.takopi/takopi.toml`:

```toml
transport = "matrix"

[transports.matrix]
homeserver = "https://matrix.example.org"
user_id = "@bot:example.org"
access_token = "syt_your_access_token"
room_ids = ["!roomid:example.org"]

# Optional: per-room engine defaults
[transports.matrix.room_engines]
"!room1:example.org" = "claude"
"!room2:example.org" = "codex"

# Optional: project-to-room binding
[transports.matrix.room_projects]
"!room1:example.org" = "myproject"
```

## Documentation

- [Matrix Transport Reference](docs/matrix.md) - Full configuration options
- [Architecture Overview](docs/architecture/overview.md) - System design
- [Development Setup](docs/development/setup.md) - Contributing guide

## License

MIT

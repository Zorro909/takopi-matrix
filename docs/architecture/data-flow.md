# Data Flow

This document describes how data flows through the takopi-matrix system.

## Message Lifecycle

### 1. Receiving Messages

```
Matrix Homeserver
       |
       | (long-poll /sync)
       v
+------+------+
| nio.sync()  |
+------+------+
       |
       v
+------+------+
| SyncResponse|
| - rooms     |
| - timeline  |
| - events    |
+------+------+
       |
       v
+-------------+
| _process_   |
| sync_       |
| response()  |
+------+------+
       |
       +----------------+
       |                |
       v                v
+------+------+  +------+------+
| Regular     |  | Encrypted   |
| Events      |  | Events      |
+------+------+  +------+------+
       |                |
       |        +-------v-------+
       |        | decrypt_event |
       |        +-------+-------+
       |                |
       +--------+-------+
                |
                v
       +--------+--------+
       | parse_room_*()  |
       | - message       |
       | - media         |
       | - audio         |
       | - reaction      |
       +--------+--------+
                |
                v
       +--------+--------+
       | MatrixIncoming  |
       | Message         |
       +-----------------+
```

### 2. Processing Messages

```
MatrixIncomingMessage
       |
       v
+------+------+
| Command     |
| Detection   |
+------+------+
       |
       +--------+--------+
       |                 |
       v                 v
+------+------+  +-------+------+
| Slash Cmd   |  | Regular Msg  |
| (/help,     |  |              |
|  /cancel)   |  |              |
+------+------+  +-------+------+
       |                 |
       v                 v
+------+------+  +-------+------+
| Command     |  | Engine       |
| Executor    |  | Resolution   |
+------+------+  +-------+------+
       |                 |
       v                 v
+------+------+  +-------+------+
| Response    |  | takopi       |
| (text)      |  | Runner       |
+------+------+  +-------+------+
       |                 |
       +--------+--------+
                |
                v
       +--------+--------+
       | MatrixTransport |
       | .send()         |
       +-----------------+
```

### 3. Sending Responses

```
MatrixTransport.send()
       |
       v
+------+------+
| Format      |
| Content     |
| - markdown  |
| - HTML      |
+------+------+
       |
       v
+------+------+
| MatrixClient|
| .send_msg() |
+------+------+
       |
       v
+------+------+
| Outbox      |
| Queue       |
+------+------+
       |
       | (rate limited)
       v
+------+------+
| nio.room_   |
| send()      |
+------+------+
       |
       | (E2EE if enabled)
       v
+------+------+
| Matrix      |
| Homeserver  |
+-------------+
```

## Streaming Updates

For long-running operations, responses are streamed via message edits:

```
Engine starts
    |
    v
+---+---+
| Send  |  -----> Initial message
| msg   |         "Processing..."
+---+---+
    |
    | (stream chunk)
    v
+---+---+
| Queue |  -----> Edit (coalesced)
| edit  |         "Processing...\n[chunk 1]"
+---+---+
    |
    | (more chunks)
    v
+---+---+
| Queue |  -----> Edit replaces previous
| edit  |         "Processing...\n[chunk 1]\n[chunk 2]"
+---+---+
    |
    | (complete)
    v
+---+---+
| Final |  -----> Final edit
| edit  |         "[Complete response]"
+-------+
```

Edit coalescing ensures only the latest content is sent, reducing Matrix API calls.

## Cancel Flow

Cancellation can be triggered two ways:

### Via Reaction

```
User adds reaction
       |
       v
+------+------+
| parse_      |
| reaction()  |
+------+------+
       |
       v
+------+------+
| Check if    |
| cancel      |
| emoji       |
+------+------+
       |
       +--------+--------+
       |                 |
       v                 v
   (cancel)         (ignore)
       |
       v
+------+------+
| Find active |
| run for     |
| target event|
+------+------+
       |
       v
+------+------+
| runner.     |
| cancel()    |
+-------------+
```

### Via Command

```
"/cancel" message
       |
       v
+------+------+
| _is_cancel_ |
| command()   |
+------+------+
       |
       v
+------+------+
| Find active |
| run for room|
+------+------+
       |
       v
+------+------+
| runner.     |
| cancel()    |
+------+------+
       |
       v
+------+------+
| Send        |
| confirmation|
+-------------+
```

## Voice Transcription Flow

```
Audio message received
       |
       v
+------+------+
| parse_room_ |
| audio()     |
+------+------+
       |
       v
+------+------+
| Download    |
| audio file  |
+------+------+
       |
       v
+------+------+
| Normalize   |
| filename    |
| extension   |
+------+------+
       |
       v
+------+------+
| OpenAI      |
| Whisper API |
+------+------+
       |
       v
+------+------+
| Transcribed |
| text        |
+------+------+
       |
       v
+------+------+
| Process as  |
| text message|
+-------------+
```

## Engine Resolution Flow

```
Message in room
       |
       v
+------+------+
| Check room  |  <---- room_prefs.json
| preferences |
+------+------+
       |
       | (if no pref)
       v
+------+------+
| Check room  |  <---- config room_projects
| project     |
| binding     |
+------+------+
       |
       | (if no binding)
       v
+------+------+
| Use default |  <---- config default_engine
| engine      |
+------+------+
       |
       v
+------+------+
| Resolve via |
| takopi      |
| router      |
+-------------+
```

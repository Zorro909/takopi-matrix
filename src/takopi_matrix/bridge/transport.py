"""Matrix transport implementation."""

from __future__ import annotations

from takopi.api import MessageRef, RenderedMessage, SendOptions

from ..client import MatrixClient


class MatrixTransport:
    """Implements Transport protocol for Matrix."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        room_id = str(channel_id)
        reply_to_event_id: str | None = None
        disable_notification = False

        if options is not None:
            disable_notification = not options.notify
            if options.reply_to is not None:
                reply_to_event_id = str(options.reply_to.message_id)
            if options.replace is not None:
                await self._client.drop_pending_edits(
                    room_id=room_id,
                    event_id=str(options.replace.message_id),
                )

        formatted_body = message.extra.get("formatted_body")

        sent = await self._client.send_message(
            room_id=room_id,
            body=message.text,
            formatted_body=formatted_body,
            reply_to_event_id=reply_to_event_id,
            disable_notification=disable_notification,
        )

        if sent is None:
            return None

        event_id = sent.get("event_id")
        if event_id is None:
            return None

        if options is not None and options.replace is not None:
            await self._client.redact_message(
                room_id=room_id,
                event_id=str(options.replace.message_id),
            )

        return MessageRef(
            channel_id=room_id,
            message_id=event_id,
            raw=sent,
        )

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None:
        room_id = str(ref.channel_id)
        event_id = str(ref.message_id)
        formatted_body = message.extra.get("formatted_body")

        edited = await self._client.edit_message(
            room_id=room_id,
            event_id=event_id,
            body=message.text,
            formatted_body=formatted_body,
            wait=wait,
        )

        if edited is None:
            return ref if not wait else None

        new_event_id = edited.get("event_id", event_id)
        return MessageRef(
            channel_id=room_id,
            message_id=new_event_id,
            raw=edited,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._client.redact_message(
            room_id=str(ref.channel_id),
            event_id=str(ref.message_id),
        )

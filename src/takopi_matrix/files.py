from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from takopi.api import get_logger

if TYPE_CHECKING:
    from .client import MatrixClient
    from .types import MatrixFile

logger = get_logger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def generate_filename_with_hash(
    original_name: str,
    content: bytes,
    dest_dir: Path,
) -> Path:
    """
    Generate unique filename with hash suffix if collision exists.

    If file.png already exists, creates file_a3f2b1.png using content hash.
    """
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    target = dest_dir / original_name

    if not target.exists():
        return target

    content_hash = hashlib.sha256(content).hexdigest()[:6]
    new_name = f"{stem}_{content_hash}{suffix}"
    return dest_dir / new_name


async def download_and_save_file(
    client: MatrixClient,
    mxc_url: str,
    original_name: str,
    dest_dir: Path,
    *,
    max_size: int = MAX_FILE_SIZE,
    file_info: dict | None = None,
) -> tuple[Path | None, str | None]:
    """
    Download file from Matrix and save to dest_dir.

    If file_info is provided (for encrypted files), the content will be decrypted.

    Returns (saved_path, error_message).
    """
    try:
        content = await client.download_file(
            mxc_url, max_size=max_size, file_info=file_info
        )
    except Exception as exc:
        logger.error(
            "matrix.file.download_error",
            mxc_url=mxc_url,
            error=str(exc),
        )
        return None, f"failed to download file: {exc}"

    if content is None:
        return None, "failed to download file"

    if len(content) > max_size:
        size_mb = max_size // (1024 * 1024)
        return None, f"file exceeds {size_mb}MB limit"

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        file_path = generate_filename_with_hash(original_name, content, dest_dir)
        file_path.write_bytes(content)
        logger.info(
            "matrix.file.saved",
            mxc_url=mxc_url,
            path=str(file_path),
            size=len(content),
        )
        return file_path, None
    except Exception as exc:
        logger.error(
            "matrix.file.save_error",
            mxc_url=mxc_url,
            error=str(exc),
        )
        return None, f"failed to save file: {exc}"


def insert_file_reference(text: str, file_path: Path) -> str:
    """
    Insert @FILENAME reference at the beginning of message text.

    Args:
        text: Original message text
        file_path: Path to the downloaded file

    Returns:
        Text with @FILENAME reference prepended
    """
    filename = file_path.name
    if text:
        return f"@{filename}\n\n{text}"
    return f"@{filename}"


async def process_attachments(
    client: MatrixClient,
    attachments: list[MatrixFile],
    dest_dir: Path,
    *,
    max_size: int = MAX_FILE_SIZE,
) -> tuple[str, list[str]]:
    """
    Process file attachments and return modified message text.

    Downloads all attachments to dest_dir and generates @FILENAME references.

    Returns:
        (text_with_references, list_of_errors)
    """
    text_parts: list[str] = []
    errors: list[str] = []

    for attachment in attachments:
        file_path, error = await download_and_save_file(
            client,
            attachment.mxc_url,
            attachment.filename,
            dest_dir,
            max_size=max_size,
            file_info=attachment.file_info,
        )
        if file_path:
            text_parts.append(f"@{file_path.name}")
        elif error:
            errors.append(f"{attachment.filename}: {error}")
            logger.warning(
                "matrix.file.process_failed",
                filename=attachment.filename,
                error=error,
            )

    return "\n".join(text_parts), errors

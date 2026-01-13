"""Tests for Matrix file download and @FILENAME handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi_matrix.files import (
    MAX_FILE_SIZE,
    download_and_save_file,
    generate_filename_with_hash,
    insert_file_reference,
    process_attachments,
)
from matrix_fixtures import FakeMatrixClient, make_matrix_file


def test_generate_filename_no_collision(tmp_path: Path) -> None:
    """Original filename used when no collision exists."""
    content = b"test content"
    result = generate_filename_with_hash("test.txt", content, tmp_path)
    assert result == tmp_path / "test.txt"


def test_generate_filename_with_collision(tmp_path: Path) -> None:
    """Hash suffix added when file already exists."""
    existing = tmp_path / "test.txt"
    existing.write_bytes(b"existing content")

    new_content = b"new content"
    result = generate_filename_with_hash("test.txt", new_content, tmp_path)

    assert result != existing
    assert result.stem.startswith("test_")
    assert result.suffix == ".txt"
    # Hash should be 6 chars
    assert len(result.stem.split("_")[1]) == 6


def test_generate_filename_hash_uniqueness(tmp_path: Path) -> None:
    """Different content produces different hashes."""
    existing = tmp_path / "file.txt"
    existing.write_bytes(b"existing")

    content1 = b"content one"
    content2 = b"content two"

    result1 = generate_filename_with_hash("file.txt", content1, tmp_path)
    result2 = generate_filename_with_hash("file.txt", content2, tmp_path)

    # Both should have hashes but different ones
    assert result1 != result2
    assert result1.stem != result2.stem


def test_generate_filename_preserves_extension(tmp_path: Path) -> None:
    """File extension is preserved with hash."""
    existing = tmp_path / "image.png"
    existing.write_bytes(b"existing")

    result = generate_filename_with_hash("image.png", b"new image", tmp_path)
    assert result.suffix == ".png"


def test_generate_filename_no_extension(tmp_path: Path) -> None:
    """Files without extension work correctly."""
    existing = tmp_path / "Makefile"
    existing.write_bytes(b"existing")

    result = generate_filename_with_hash("Makefile", b"new content", tmp_path)
    assert result.stem.startswith("Makefile_")
    assert result.suffix == ""


@pytest.mark.anyio
async def test_download_and_save_file_success(tmp_path: Path) -> None:
    """Happy path: file downloads and saves correctly."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/abc123"] = b"file content"

    path, error = await download_and_save_file(
        client,  # type: ignore[arg-type]
        "mxc://example.org/abc123",
        "test.txt",
        tmp_path,
    )

    assert error is None
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"file content"
    assert path.name == "test.txt"


@pytest.mark.anyio
async def test_download_and_save_file_network_error(tmp_path: Path) -> None:
    """Network error returns error message."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/fail"] = ConnectionError("timeout")

    path, error = await download_and_save_file(
        client,  # type: ignore[arg-type]
        "mxc://example.org/fail",
        "test.txt",
        tmp_path,
    )

    assert path is None
    assert error is not None
    assert "failed to download" in error.lower()


@pytest.mark.anyio
async def test_download_and_save_file_returns_none(tmp_path: Path) -> None:
    """None response from download returns error."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/none"] = None  # type: ignore[assignment]

    path, error = await download_and_save_file(
        client,  # type: ignore[arg-type]
        "mxc://example.org/none",
        "test.txt",
        tmp_path,
    )

    assert path is None
    assert error is not None


@pytest.mark.anyio
async def test_download_and_save_file_size_exceeded(tmp_path: Path) -> None:
    """File exceeding max size returns error."""
    client = FakeMatrixClient()
    large_content = b"x" * (MAX_FILE_SIZE + 1000)
    client.download_responses["mxc://example.org/large"] = large_content

    path, error = await download_and_save_file(
        client,  # type: ignore[arg-type]
        "mxc://example.org/large",
        "large.bin",
        tmp_path,
        max_size=MAX_FILE_SIZE,
    )

    assert path is None
    assert error is not None
    assert "limit" in error.lower()


@pytest.mark.anyio
async def test_download_and_save_file_creates_dir(tmp_path: Path) -> None:
    """Directory is created if it doesn't exist."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/abc"] = b"content"

    nested_dir = tmp_path / "nested" / "deep" / "dir"
    assert not nested_dir.exists()

    path, error = await download_and_save_file(
        client,  # type: ignore[arg-type]
        "mxc://example.org/abc",
        "file.txt",
        nested_dir,
    )

    assert error is None
    assert path is not None
    assert nested_dir.exists()
    assert path.exists()


def test_insert_file_reference_with_text() -> None:
    """@FILENAME prepended to existing text."""
    result = insert_file_reference("analyze this", Path("image.png"))
    assert result == "@image.png\n\nanalyze this"


def test_insert_file_reference_empty_text() -> None:
    """@FILENAME alone when text is empty."""
    result = insert_file_reference("", Path("doc.pdf"))
    assert result == "@doc.pdf"


def test_insert_file_reference_preserves_filename() -> None:
    """Filename with special characters preserved."""
    result = insert_file_reference("text", Path("file-with-dash_and_underscore.txt"))
    assert "@file-with-dash_and_underscore.txt" in result


@pytest.mark.anyio
async def test_process_attachments_single(tmp_path: Path) -> None:
    """Single attachment processed correctly."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/file1"] = b"content1"

    attachments = [make_matrix_file(mxc_url="mxc://example.org/file1", filename="a.txt")]

    text, errors = await process_attachments(
        client,  # type: ignore[arg-type]
        attachments,
        tmp_path,
    )

    assert errors == []
    assert "@a.txt" in text


@pytest.mark.anyio
async def test_process_attachments_multiple(tmp_path: Path) -> None:
    """Multiple attachments all processed."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/file1"] = b"content1"
    client.download_responses["mxc://example.org/file2"] = b"content2"

    attachments = [
        make_matrix_file(mxc_url="mxc://example.org/file1", filename="a.txt"),
        make_matrix_file(mxc_url="mxc://example.org/file2", filename="b.txt"),
    ]

    text, errors = await process_attachments(
        client,  # type: ignore[arg-type]
        attachments,
        tmp_path,
    )

    assert errors == []
    assert "@a.txt" in text
    assert "@b.txt" in text


@pytest.mark.anyio
async def test_process_attachments_partial_fail(tmp_path: Path) -> None:
    """Some attachments succeed, some fail."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/good"] = b"good content"
    client.download_responses["mxc://example.org/bad"] = ConnectionError("fail")

    attachments = [
        make_matrix_file(mxc_url="mxc://example.org/good", filename="good.txt"),
        make_matrix_file(mxc_url="mxc://example.org/bad", filename="bad.txt"),
    ]

    text, errors = await process_attachments(
        client,  # type: ignore[arg-type]
        attachments,
        tmp_path,
    )

    assert "@good.txt" in text
    assert len(errors) == 1
    assert "bad.txt" in errors[0]


@pytest.mark.anyio
async def test_process_attachments_empty_list(tmp_path: Path) -> None:
    """Empty attachment list returns empty text."""
    client = FakeMatrixClient()

    text, errors = await process_attachments(
        client,  # type: ignore[arg-type]
        [],
        tmp_path,
    )

    assert text == ""
    assert errors == []


@pytest.mark.anyio
async def test_process_attachments_uses_max_size(tmp_path: Path) -> None:
    """Custom max_size is passed to download."""
    client = FakeMatrixClient()
    client.download_responses["mxc://example.org/file"] = b"x" * 100

    attachments = [make_matrix_file(mxc_url="mxc://example.org/file")]

    await process_attachments(
        client,  # type: ignore[arg-type]
        attachments,
        tmp_path,
        max_size=1000,
    )

    # Check that download was called with our max_size
    assert client.download_calls[0][1] == 1000

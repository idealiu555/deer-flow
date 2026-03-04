from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import paths as paths_module
from src.gateway.routers import uploads


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(uploads.router)
    return TestClient(app)


@pytest.fixture
def isolated_deer_flow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    yield tmp_path
    monkeypatch.setattr(paths_module, "_paths", None)


def test_upload_files_writes_thread_storage_and_skips_local_sandbox_sync(
    client: TestClient, isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = MagicMock()
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)

    response = client.post(
        "/api/threads/thread-local/uploads",
        files=[("files", ("notes.txt", b"hello uploads", "text/plain"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert len(payload["files"]) == 1
    assert payload["files"][0]["filename"] == "notes.txt"

    written_file = isolated_deer_flow_home / "threads" / "thread-local" / "user-data" / "uploads" / "notes.txt"
    assert written_file.read_bytes() == b"hello uploads"

    sandbox.update_file.assert_not_called()


def test_upload_files_syncs_non_local_sandbox_and_marks_markdown_file(
    client: TestClient, isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = MagicMock()
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    monkeypatch.setattr(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert))

    response = client.post(
        "/api/threads/thread-aio/uploads",
        files=[("files", ("report.pdf", b"pdf-bytes", "application/pdf"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert len(payload["files"]) == 1
    file_info = payload["files"][0]
    assert file_info["filename"] == "report.pdf"
    assert file_info["markdown_file"] == "report.md"

    uploads_dir = isolated_deer_flow_home / "threads" / "thread-aio" / "user-data" / "uploads"
    assert (uploads_dir / "report.pdf").read_bytes() == b"pdf-bytes"
    assert (uploads_dir / "report.md").read_text(encoding="utf-8") == "converted"

    sandbox.update_file.assert_any_call("/mnt/user-data/uploads/report.pdf", b"pdf-bytes")
    sandbox.update_file.assert_any_call("/mnt/user-data/uploads/report.md", b"converted")


def test_upload_files_rejects_dotdot_and_dot_filenames(
    client: TestClient, isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = MagicMock()
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)

    for bad_name in ["..", "."]:
        response = client.post(
            "/api/threads/thread-safe/uploads",
            files=[("files", (bad_name, b"data", "application/octet-stream"))],
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["files"] == [], f"Expected no files for unsafe filename {bad_name!r}"

    response = client.post(
        "/api/threads/thread-safe/uploads",
        files=[("files", ("../etc/passwd", b"data", "application/octet-stream"))],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert len(payload["files"]) == 1
    assert payload["files"][0]["filename"] == "passwd"

    uploads_dir = isolated_deer_flow_home / "threads" / "thread-safe" / "user-data" / "uploads"
    assert sorted([entry.name for entry in uploads_dir.iterdir()]) == ["passwd"]

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from voice_agent.server.upload_limits import UploadPolicy, read_upload_with_limits, safe_upload_suffix


class MemoryUpload(UploadFile):
    def __init__(self, filename: str, data: bytes):
        super().__init__(file=None, filename=filename, headers=Headers({}))
        self._data = data

    async def read(self, size: int = -1) -> bytes:  # noqa: ARG002
        return self._data


@pytest.mark.asyncio
async def test_read_upload_with_limits_accepts_valid_file():
    policy = UploadPolicy.build("test", 10, [".wav"])
    upload = MemoryUpload("clip.wav", b"12345")

    assert safe_upload_suffix(upload, policy=policy) == ".wav"
    assert await read_upload_with_limits(upload, policy) == b"12345"


@pytest.mark.asyncio
async def test_read_upload_with_limits_rejects_empty_file():
    policy = UploadPolicy.build("test", 10, [".wav"])
    upload = MemoryUpload("clip.wav", b"")

    with pytest.raises(HTTPException) as exc:
        await read_upload_with_limits(upload, policy)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_read_upload_with_limits_rejects_large_file():
    policy = UploadPolicy.build("test", 3, [".wav"])
    upload = MemoryUpload("clip.wav", b"1234")

    with pytest.raises(HTTPException) as exc:
        await read_upload_with_limits(upload, policy)
    assert exc.value.status_code == 413


def test_safe_upload_suffix_rejects_unsupported_extension():
    policy = UploadPolicy.build("test", 10, [".wav"])
    upload = MemoryUpload("clip.exe", b"123")

    with pytest.raises(HTTPException) as exc:
        safe_upload_suffix(upload, policy=policy)
    assert exc.value.status_code == 415

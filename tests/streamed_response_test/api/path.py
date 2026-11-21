from lapis import Request, StreamedResponse

from typing import AsyncGenerator


async def get_stream(req: Request) -> AsyncGenerator[bytes, None]:
    yield b"Hello"
    yield b" World!"


async def GET(req: Request) -> StreamedResponse:
    return StreamedResponse(stream=get_stream, status_code=200)

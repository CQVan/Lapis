from lapis import Request, StreamedResponse

import asyncio
from typing import AsyncGenerator


async def get_stream(req: Request) -> AsyncGenerator[bytes, None]:
    await asyncio.sleep(1)
    yield b"Hello"
    await asyncio.sleep(1)
    yield b" World!"
    await asyncio.sleep(1)


async def GET(req: Request) -> StreamedResponse:
    return StreamedResponse(stream=get_stream, status_code=200)

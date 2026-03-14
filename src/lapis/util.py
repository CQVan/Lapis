import asyncio
import socket
from datetime import datetime
from urllib.parse import urlparse

__column_width: int = 20

def print_connection_event(*args):
    current_time = datetime.now().strftime("%H:%M:%S")
    
    if not args:
        print(current_time)
        return

    first = f"{str(args[0]):<{__column_width}}"

    middle = "".join(f"{str(arg):^{__column_width}}" for arg in args[1:-1])

    last = f"{str(args[-1]):<{__column_width}}" if len(args) > 1 else ""

    row = f"{current_time}  {first}{middle}{last}"
    print(row)

def string_to_clickable_url(url: str, text : str = None) -> str:
    """
    Converts a string to a clickable URL if it is a valid URL, otherwise returns the original string.
    """
    
    display_text = text if text is not None else url
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL")

    return f'\033]8;;{url}\033\\{display_text}\033]8;;\033\\'

async def read_exact(socket: socket.socket, bufsize: int) -> bytes:
    data = bytearray()

    try:
        loop = asyncio.get_running_loop()
        recv = lambda n: loop.sock_recv(socket, n)
        async_mode = True
    except RuntimeError:
        recv = socket.recv
        async_mode = False

    while len(data) < bufsize:
        if async_mode:
            chunk = await recv(bufsize - len(data))
        else:
            chunk = recv(bufsize - len(data))

        if not chunk:
            raise ConnectionResetError("Connection Was Reset!")

        data.extend(chunk)

    return bytes(data)
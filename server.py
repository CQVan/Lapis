import select
import socket
import asyncio
import inspect
import runpy
from multiprocessing import Process
from datetime import datetime
from server_types import ServerConfig, Request, Response

class Server:
    s: socket.socket = None
    cfg: ServerConfig = ServerConfig()

    def __init__(self, config: ServerConfig | None = None):
        if config is not None:
            self.cfg = config

    def run(self, ip: str, port: int):
        self.s = socket.socket()
        self.s.bind((ip, port))
        self.s.listen()
        print(f"Server is now listening on http://{ip}:{port}")

        try:
            while True:
                readable, _, _ = select.select([self.s], [], [], 0.1)
                if self.s in readable:
                    client, _ = self.s.accept()
                    p = Process(target=self._handle_request, args=(client,))
                    p.daemon = True
                    p.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.__close()

    def _handle_request(self, client: socket.socket):
        try:
            data = client.recv(self.cfg.max_request_size)
            current_time = datetime.now().strftime("%H:%M:%S")
            request = Request(data=data)
            print(f"{current_time} {request.method} {request.base_url} {client.getpeername()[0]}")

            try:
                route = runpy.run_path(f"{self.cfg.dir}{request.base_url}\\route.py")
                if request.method not in route:
                    raise RuntimeError("Method not found")
                
                if not inspect.iscoroutinefunction(route[request.method]):
                    raise RuntimeError("Method is not asynchronous!")
                
                response : Response = asyncio.run(route[request.method](request))

                client.sendall(response.to_bytes())

            except Exception as e:
                print(f"Error handling client: {e}")

        except FileNotFoundError:
            # TODO: send 404 status code
            response : Response = Response()
            client.sendall(response.to_bytes())
            pass

        except Exception as e:
            print(f"Error handling client: {e}")
        finally:
            client.close()

    def __close(self):
        if self.s is not None:
            try:
                print("Closing Server...")
                self.s.close()
            except Exception as e:
                print(f"Error when closing socket: {e}")

if __name__ == "__main__":
    server = Server()
    server.run("localhost", 80)

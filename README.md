Lapis is a file-based REST API framework inspired by Modern Serverless Cloud Services

To create a basic Lapis server, create a folder called _api_ and create python script named _path.py_
then within your main folder create a python script to start the server (we will call this script _main.py_ in our example)

Your project directory should look like this:

```
project-root/
|-- api/
|  |-- path.py
`-- main.py
```

Then within the _api/path.py_ file create your first GET api endpoint by adding the following code:

```py
from lapis import Response, Request

async def GET (req : Request) -> Response:
    return Response(status_code=200, body="Hello World!")
```

Finally by adding the following code to _main.py_ and running it:

```py
from lapis import Lapis

server = Lapis()

server.run("localhost", 80)
```

You can now send an HTTP GET request to localhost:80 and recieve the famous **Hello World!** response!

## Development

Install the project and its test and lint dependencies:

```sh
uv sync --locked --extra test --group lint
```

Run the tests, format and lint the code, or build distributions directly with uv:

```sh
uv run --locked black .                     # Formats Code
uv run --locked coverage run -m pytest      # Checks Test Coverage
uv run --locked pytest src tests            # Runs Tests
uv run --locked pylint src tests            # Lints Code
uv build                                    # Builds Package
```

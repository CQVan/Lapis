
from http import HTTPStatus
import json
from server_types import Response, Request

async def GET(request : Request) -> Response:

    example_result = {
        "slugs found": request.slugs,
    }

    return Response(status_code=200, body=json.dumps(example_result))
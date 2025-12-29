
from http import HTTPStatus
import json
from server_types import Response, Request

async def GET(request : Request) -> Response:

    example_result = {
        "gay": True,
        "slugs": request.slugs,
        "url": request.base_url
    }

    return Response(status_code=200, body=json.dumps(example_result))
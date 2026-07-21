import azure.functions as func

from app import app as fastapi_app


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.function_name(name="fastapi_proxy")
@app.route(
    route="{*route}",
    methods=[func.HttpMethod.GET, func.HttpMethod.POST, func.HttpMethod.DELETE, func.HttpMethod.OPTIONS],
    auth_level=func.AuthLevel.ANONYMOUS,
)
async def fastapi_proxy(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return await func.AsgiMiddleware(fastapi_app).handle_async(req, context)

from fastapi.responses import JSONResponse

class UploadBodyLimitMiddleware:
    """Bound multipart input before the parser can spool it to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/uploads"):
            return await self.app(scope, receive, send)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > 11 * 1024 * 1024:
                request_id = scope.get("state", {}).get("request_id", "")
                response = JSONResponse(status_code=413, content={"error": {
                    "code": "PAYLOAD_TOO_LARGE", "message": "Upload request exceeds the size limit.",
                    "requestId": request_id, "details": []}})
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0
        async def replay():
            nonlocal index
            if index < len(chunks):
                message = chunks[index]
                index += 1
                return message
            return await receive()
        await self.app(scope, replay, send)

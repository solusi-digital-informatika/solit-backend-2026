class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []

def invalid(message: str, path="body"):
    raise DomainError(400, "VALIDATION_ERROR", message, [{"path": path, "message": message}])

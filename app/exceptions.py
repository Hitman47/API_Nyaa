from __future__ import annotations


class APIError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, detail: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class InvalidQuery(APIError):
    status_code = 400
    code = "INVALID_QUERY"


class InvalidParameter(APIError):
    status_code = 422
    code = "INVALID_PARAMETER"


class AuthRequired(APIError):
    status_code = 401
    code = "AUTH_REQUIRED"


class ResourceNotFound(APIError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class OutOfScopeResource(APIError):
    status_code = 404
    code = "OUT_OF_SCOPE_RESOURCE"


class RateLimited(APIError):
    status_code = 429
    code = "RATE_LIMITED"

    def __init__(self, detail: str, *, retry_after: int = 1):
        super().__init__(detail)
        self.retry_after = max(1, retry_after)


class UpstreamFetchError(APIError):
    status_code = 502
    code = "UPSTREAM_FETCH_ERROR"


class UpstreamParseError(APIError):
    status_code = 502
    code = "UPSTREAM_PARSE_ERROR"


class StorageLimitReached(APIError):
    status_code = 503
    code = "STORAGE_LIMIT_REACHED"

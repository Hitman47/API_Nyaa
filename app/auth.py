from __future__ import annotations

import secrets

from fastapi import Header, Request

from app.exceptions import AuthRequired


async def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    expected = request.app.state.settings.api_token_value
    if not expected:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthRequired("Missing or invalid bearer token.")
    supplied = authorization.split(" ", 1)[1]
    if not secrets.compare_digest(supplied, expected):
        raise AuthRequired("Missing or invalid bearer token.")


async def reject_scope_parameters(request: Request) -> None:
    banned = {"category", "c", "cats", "uploader", "u", "user"}
    found = sorted(banned.intersection(request.query_params.keys()))
    if found:
        from app.exceptions import InvalidParameter

        raise InvalidParameter(
            "Category and uploader parameters are not public; API_Nyaa is permanently scoped to c=3_1."
        )

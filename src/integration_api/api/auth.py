import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from integration_api.core.config import Settings

_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def require_api_token(request: Request, credentials: Credentials) -> None:
    settings: Settings = request.app.state.settings
    configured = settings.integration_api_token
    supplied = credentials.credentials if credentials is not None else ""
    valid = configured is not None and secrets.compare_digest(
        supplied, configured.get_secret_value()
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

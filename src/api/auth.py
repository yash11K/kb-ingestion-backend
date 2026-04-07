"""Simple access-code authentication.

Flow:
1. Frontend POSTs {"code": "..."} to /api/v1/auth/verify
2. If the code matches, backend returns a short-lived token
3. Frontend sends that token as  Authorization: Bearer <token>  on every request
"""

import secrets
import time

from fastapi import APIRouter, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# ── Hardcoded access code — change this to whatever you want ──
ACCESS_CODE = "tariffs-are-transitory"

# In-memory token store: token -> expiry timestamp
_tokens: dict[str, float] = {}
TOKEN_TTL = 60 * 60 * 12  # 12 hours

_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])


class CodeRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/verify", response_model=TokenResponse)
async def verify_code(body: CodeRequest):
    """Validate the access code and return a bearer token."""
    if not secrets.compare_digest(body.code, ACCESS_CODE):
        raise HTTPException(status_code=401, detail="Invalid access code")

    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL

    # Lazy cleanup of expired tokens
    now = time.time()
    expired = [t for t, exp in _tokens.items() if exp < now]
    for t in expired:
        _tokens.pop(t, None)

    return TokenResponse(token=token, expires_in=TOKEN_TTL)


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """Dependency — rejects requests without a valid bearer token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = credentials.credentials
    expiry = _tokens.get(token)

    if expiry is None or expiry < time.time():
        _tokens.pop(token, None)
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return token

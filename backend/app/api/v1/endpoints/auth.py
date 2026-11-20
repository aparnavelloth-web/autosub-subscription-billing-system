"""
Authentication & Authorization API Endpoints.
Provides:
- POST /api/v1/auth/signup: User registration.
- POST /api/v1/auth/signin: User login returning JWT access token.
- POST /api/v1/auth/login: OAuth2/JSON login alias compatible with Swagger UI and web forms.
- GET  /api/v1/auth/me: Retrieve authenticated user profile.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.api.deps import get_current_active_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse
from app.services.auth_service import (
    AuthService,
    DuplicateUserError,
    InactiveUserError,
    InvalidCredentialsError,
)

router = APIRouter()


def _get_client_ip(request: Request) -> Optional[str]:
    """Extracts client IP address safely from request headers or client socket."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    description="Registers a new user with email, full name, and strong password. Passwords are securely hashed via bcrypt.",
)
def signup(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_addr = _get_client_ip(request)
    try:
        user = AuthService.create_user(
            db=db,
            payload=payload,
            actor="auth.signup",
            ip_address=ip_addr,
        )
        return user
    except DuplicateUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )


@router.post(
    "/signin",
    response_model=Token,
    status_code=status.HTTP_200_OK,
    summary="Sign in to obtain JWT access token",
    description="Authenticates with email and password, returning a signed JWT access token.",
)
def signin(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_addr = _get_client_ip(request)
    try:
        user = AuthService.authenticate_user(
            db=db,
            email=payload.email,
            password=payload.password,
            actor="auth.signin",
            ip_address=ip_addr,
        )
        return AuthService.issue_token(user)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


@router.post(
    "/login",
    response_model=Token,
    status_code=status.HTTP_200_OK,
    summary="Sign in alias (JSON or OAuth2 form)",
    description="Alias for signin supporting JSON body or application/x-www-form-urlencoded credentials.",
)
def login_alias(
    request: Request,
    db: Session = Depends(get_db),
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
):
    """
    Accepts form data (OAuth2 standard `username` field as email) or falls back to JSON body.
    """
    ip_addr = _get_client_ip(request)

    # 1. Check if form-data was posted (standard OAuth2 / Swagger UI)
    if username is not None and password is not None:
        email_val = username
        pw_val = password
    else:
        # 2. Otherwise parse JSON request body
        try:
            body = getattr(request, "_json_parsed", None)
        except Exception:
            body = None

        if body is None:
            # Need to read and parse body synchronously if available
            import json
            try:
                # Fastapi cache
                raw = getattr(request, "_body", b"")
                if not raw and hasattr(request, "body"):
                    pass
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                email_val = parsed.get("email", "")
                pw_val = parsed.get("password", "")
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing credentials in request body.",
                )
        else:
            email_val = body.get("email", "")
            pw_val = body.get("password", "")

    if not email_val or not pw_val:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both email/username and password are required.",
        )

    try:
        user = AuthService.authenticate_user(
            db=db,
            email=email_val,
            password=pw_val,
            actor="auth.login",
            ip_address=ip_addr,
        )
        return AuthService.issue_token(user)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
    description="Returns the profile information of the currently authenticated user.",
)
def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
):
    return current_user

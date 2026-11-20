"""
Authentication Service.
Handles user registration, credential authentication, JWT token generation,
and audit logging.
"""
from typing import Optional
import logging
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserResponse

logger = logging.getLogger(__name__)


class DuplicateUserError(Exception):
    """Raised when registering an account with an already registered email."""
    pass


class InvalidCredentialsError(Exception):
    """Raised when authentication credentials fail validation."""
    pass


class InactiveUserError(Exception):
    """Raised when attempting authentication on an inactive or deactivated account."""
    pass


class AuthService:
    """Modular service orchestrating authentication and user identity."""

    @staticmethod
    def create_user(
        db: Session,
        payload: UserCreate,
        actor: str = "api",
        ip_address: Optional[str] = None,
    ) -> User:
        """
        Registers a new user account with hashed password and writes an immutable audit record.
        Prevents duplicate registration by checking normalized case-insensitive email.
        """
        normalized_email = payload.email.strip().lower()

        existing = (
            db.query(User)
            .filter(func.lower(User.email) == normalized_email)
            .first()
        )
        if existing is not None:
            raise DuplicateUserError(f"User with email '{normalized_email}' already exists.")

        hashed_password = get_password_hash(payload.password)

        user = User(
            email=normalized_email,
            hashed_password=hashed_password,
            full_name=payload.full_name.strip(),
            is_active=True,
            is_superuser=False,
        )
        db.add(user)
        db.flush()

        # Audit log registration without ever recording plaintext or hashed passwords
        audit = AuditLog(
            entity_type="user",
            entity_id=user.id,
            action="user.signup",
            actor=actor,
            ip_address=ip_address,
            new_values={
                "email": user.email,
                "full_name": user.full_name,
                "is_active": user.is_active,
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(user)

        logger.info("Successfully registered new user: %s (id=%d)", user.email, user.id)
        return user

    @staticmethod
    def authenticate_user(
        db: Session,
        email: str,
        password: str,
        actor: str = "api",
        ip_address: Optional[str] = None,
    ) -> User:
        """
        Verifies credentials against persistent database hash.
        Checks active status and returns User on success.
        """
        normalized_email = email.strip().lower()

        user = (
            db.query(User)
            .filter(func.lower(User.email) == normalized_email)
            .first()
        )

        if user is None or not verify_password(password, user.hashed_password):
            audit = AuditLog(
                entity_type="user",
                entity_id=user.id if user else 0,
                action="user.signin_failed",
                actor=actor,
                ip_address=ip_address,
                new_values={"email": normalized_email, "reason": "invalid_credentials"},
            )
            db.add(audit)
            db.commit()
            raise InvalidCredentialsError("Invalid email or password.")

        if not user.is_active:
            audit = AuditLog(
                entity_type="user",
                entity_id=user.id,
                action="user.signin_inactive_blocked",
                actor=actor,
                ip_address=ip_address,
                new_values={"email": normalized_email, "reason": "account_inactive"},
            )
            db.add(audit)
            db.commit()
            raise InactiveUserError("User account is inactive. Please contact support.")

        audit = AuditLog(
            entity_type="user",
            entity_id=user.id,
            action="user.signin_success",
            actor=actor,
            ip_address=ip_address,
            new_values={"email": normalized_email},
        )
        db.add(audit)
        db.commit()

        logger.info("User authenticated successfully: %s (id=%d)", user.email, user.id)
        return user

    @staticmethod
    def issue_token(user: User) -> Token:
        """
        Issues a signed JWT access token for the given user.
        """
        expires_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        access_token = create_access_token(
            subject=user.email,
            extra_claims={
                "user_id": user.id,
                "full_name": user.full_name,
                "is_superuser": user.is_superuser,
            },
        )
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_seconds,
            user=UserResponse.model_validate(user),
        )

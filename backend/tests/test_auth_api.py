"""
Unit and Integration Tests for Authentication & Authorization Module:
- User signup with validation, email normalization, and duplicate rejection.
- Password hashing security (bcrypt) and hash non-exposure in all responses.
- Signin with valid credentials, incorrect password, non-existent user, and inactive account.
- JWT access token generation, claims validation, expiration, and tampering detection.
- Protected route guards (/api/v1/auth/me) with and without valid Bearer tokens.
- Audit logging for authentication actions without recording plaintext or hashed passwords.
Executes strictly against subscription_billing_test_db.
"""
from datetime import datetime, timedelta, timezone
import pytest
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.audit_log import AuditLog
from app.models.user import User


def test_user_password_hashing_and_verification():
    """Verify native bcrypt password hashing produces valid hashes and constant-time checks."""
    plain = "SuperSecretPassword123!"
    hashed = get_password_hash(plain)

    assert hashed != plain
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False
    assert verify_password("", hashed) is False


def test_user_signup_success_and_password_non_exposure(api_client, db_session):
    """Verify successful user registration returns 201 and never leaks password hashes."""
    payload = {
        "email": "Admin.Billing@Example.COM",
        "full_name": "Billing Administrator",
        "password": "SecurePassword2026!",
    }
    response = api_client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["id"] > 0
    assert data["email"] == "admin.billing@example.com"  # Normalized to lowercase
    assert data["full_name"] == "Billing Administrator"
    assert data["is_active"] is True
    assert data["is_superuser"] is False
    assert "password" not in data
    assert "hashed_password" not in data

    # Verify database persistence & password hashing
    user_in_db = db_session.get(User, data["id"])
    assert user_in_db is not None
    assert user_in_db.email == "admin.billing@example.com"
    assert user_in_db.hashed_password != payload["password"]
    assert verify_password(payload["password"], user_in_db.hashed_password) is True

    # Verify audit log creation without exposing password
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "user", AuditLog.entity_id == user_in_db.id)
        .first()
    )
    assert audit is not None
    assert audit.action == "user.signup"
    assert "password" not in str(audit.new_values)
    assert "hashed_password" not in str(audit.new_values)


def test_user_signup_duplicate_email_rejected(api_client):
    """Verify registering with an existing email returns 409 Conflict."""
    payload = {
        "email": "duplicate.user@example.com",
        "full_name": "First User",
        "password": "Password1234!",
    }
    r1 = api_client.post("/api/v1/auth/signup", json=payload)
    assert r1.status_code == 201

    # Case-insensitive duplicate attempt
    payload_dup = {
        "email": "DUPLICATE.USER@EXAMPLE.COM",
        "full_name": "Duplicate User",
        "password": "DifferentPassword1234!",
    }
    r2 = api_client.post("/api/v1/auth/signup", json=payload_dup)
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"].lower()


def test_user_signup_validation_invalid_email_and_short_password(api_client):
    """Verify registration rejects invalid emails and short passwords with 422."""
    # Invalid email format
    r_bad_email = api_client.post(
        "/api/v1/auth/signup",
        json={"email": "invalid-email-no-at", "full_name": "User", "password": "Password123!"},
    )
    assert r_bad_email.status_code == 422

    # Password less than 8 characters
    r_short_pw = api_client.post(
        "/api/v1/auth/signup",
        json={"email": "valid@example.com", "full_name": "User", "password": "short"},
    )
    assert r_short_pw.status_code == 422

    # Empty full name
    r_empty_name = api_client.post(
        "/api/v1/auth/signup",
        json={"email": "valid@example.com", "full_name": "   ", "password": "Password123!"},
    )
    assert r_empty_name.status_code == 422


def test_user_signin_success_and_jwt_issuance(api_client):
    """Verify valid credentials return 200 with JWT access token and user metadata."""
    signup_data = {
        "email": "signin.test@example.com",
        "full_name": "Signin Test User",
        "password": "ValidPassword2026!",
    }
    reg = api_client.post("/api/v1/auth/signup", json=signup_data)
    assert reg.status_code == 201

    login_resp = api_client.post(
        "/api/v1/auth/signin",
        json={"email": "SIGNIN.TEST@example.com", "password": "ValidPassword2026!"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()

    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"
    assert token_data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert token_data["user"]["email"] == "signin.test@example.com"
    assert token_data["user"]["full_name"] == "Signin Test User"
    assert "password" not in token_data["user"]
    assert "hashed_password" not in token_data["user"]


def test_user_signin_invalid_credentials_rejected(api_client):
    """Verify wrong password or non-existent user returns 401 Unauthorized with WWW-Authenticate header."""
    # Non-existent email
    r_none = api_client.post(
        "/api/v1/auth/signin",
        json={"email": "nobody@example.com", "password": "Password123!"},
    )
    assert r_none.status_code == 401
    assert "WWW-Authenticate" in r_none.headers
    assert "Invalid email or password" in r_none.json()["detail"]

    # Existing user, wrong password
    api_client.post(
        "/api/v1/auth/signup",
        json={"email": "existing@example.com", "full_name": "User", "password": "CorrectPassword123!"},
    )
    r_wrong = api_client.post(
        "/api/v1/auth/signin",
        json={"email": "existing@example.com", "password": "WrongPassword123!"},
    )
    assert r_wrong.status_code == 401
    assert "Invalid email or password" in r_wrong.json()["detail"]


def test_user_signin_inactive_user_blocked(api_client, db_session):
    """Verify deactivated user account receives 403 Forbidden upon signin attempt."""
    email = "inactive.user@example.com"
    user = User(
        email=email,
        full_name="Deactivated User",
        hashed_password=get_password_hash("Password123!"),
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    resp = api_client.post(
        "/api/v1/auth/signin",
        json={"email": email, "password": "Password123!"},
    )
    assert resp.status_code == 403
    assert "inactive" in resp.json()["detail"].lower()


def test_protected_route_with_valid_token(api_client):
    """Verify GET /api/v1/auth/me returns the profile of the authenticated user."""
    user_data = {
        "email": "profile.check@example.com",
        "full_name": "Profile Owner",
        "password": "Password123!",
    }
    api_client.post("/api/v1/auth/signup", json=user_data)
    login = api_client.post(
        "/api/v1/auth/signin",
        json={"email": user_data["email"], "password": user_data["password"]},
    )
    token = login.json()["access_token"]

    # Authorized call
    headers = {"Authorization": f"Bearer {token}"}
    resp = api_client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["email"] == user_data["email"]
    assert profile["full_name"] == user_data["full_name"]
    assert profile["is_active"] is True
    assert "password" not in profile
    assert "hashed_password" not in profile


def test_protected_route_missing_invalid_and_tampered_token(api_client):
    """Verify missing, malformed, or tampered JWT tokens return 401 Unauthorized."""
    # 1. Missing Authorization header
    r_missing = api_client.get("/api/v1/auth/me")
    assert r_missing.status_code == 401
    assert "WWW-Authenticate" in r_missing.headers

    # 2. Malformed token string
    r_malformed = api_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt"})
    assert r_malformed.status_code == 401

    # 3. Tampered token signature
    valid_token = create_access_token("fake.user@example.com")
    tampered_token = valid_token[:-4] + "ABCD"
    r_tampered = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered_token}"})
    assert r_tampered.status_code == 401


def test_protected_route_expired_token_rejected(api_client):
    """Verify expired JWT tokens are explicitly rejected with 401."""
    # Generate token that expired 10 minutes ago
    expired_token = create_access_token(
        subject="expired.user@example.com",
        expires_delta=timedelta(minutes=-10),
    )
    resp = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_login_alias_endpoint_oauth2_form_and_json(api_client):
    """Verify /api/v1/auth/login works with both OAuth2 form data and JSON payloads."""
    email = "alias.user@example.com"
    pw = "AliasPassword123!"
    api_client.post(
        "/api/v1/auth/signup",
        json={"email": email, "full_name": "Alias User", "password": pw},
    )

    # Form-data login (OAuth2 standard: username & password)
    r_form = api_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": pw},
    )
    assert r_form.status_code == 200
    assert "access_token" in r_form.json()

    # Direct JSON signin
    r_signin = api_client.post(
        "/api/v1/auth/signin",
        json={"email": email, "password": pw},
    )
    assert r_signin.status_code == 200
    assert "access_token" in r_signin.json()


def test_oversized_passwords_rejected_without_silent_truncation(api_client):
    """Verify passwords exceeding 72 UTF-8 bytes are rejected with error instead of silently truncated."""
    oversized_password = "A" * 73
    assert len(oversized_password.encode("utf-8")) == 73

    # Direct bcrypt utility rejects with ValueError
    with pytest.raises(ValueError, match="Password cannot exceed 72 UTF-8 bytes"):
        get_password_hash(oversized_password)

    # Verification safely returns False for oversized plain password
    valid_hash = get_password_hash("ValidPassword123!")
    assert verify_password(oversized_password, valid_hash) is False

    # Signup API rejects oversized password with 422
    r_signup = api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "oversized@example.com",
            "full_name": "Oversized User",
            "password": oversized_password,
        },
    )
    assert r_signup.status_code == 422
    assert "72 utf-8 bytes" in str(r_signup.json()).lower()

    # Signin API rejects oversized password with 422
    r_signin = api_client.post(
        "/api/v1/auth/signin",
        json={
            "email": "oversized@example.com",
            "password": oversized_password,
        },
    )
    assert r_signin.status_code == 422


def test_production_environment_rejects_default_secret_key():
    """Verify Settings raises validation error if production uses default or insecure SECRET_KEY."""
    from app.core.config import Settings

    # Default development key in production environment is blocked
    with pytest.raises(ValueError, match="Production environment cannot use a default or weak SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="dev_jwt_secret_key_change_in_production",
        )

    # Weak/short key (< 32 chars) in production is blocked
    with pytest.raises(ValueError, match="Production environment cannot use a default or weak SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="too_short_secret_key",
        )

    # Cryptographically secure key (32+ chars) and non-default webhook secret succeeds
    prod_settings = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="super_secret_production_jwt_key_with_sufficient_entropy_64",
        MOCK_PAYMENT_WEBHOOK_SECRET="super_secret_production_webhook_key_with_sufficient_entropy",
    )
    assert prod_settings.ENVIRONMENT == "production"
    assert prod_settings.SECRET_KEY.startswith("super_secret_production_jwt_key")


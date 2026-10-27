"""Single-use, 15-minute codes. Persist only an HMAC, never plaintext codes."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from ..config import get_settings


def code_digest(user_id, code):
    return hmac.new(get_settings().jwt_secret.encode(), f"email-verification:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def deliver_code(email, code):
    settings = get_settings()
    text = f"Your DataGuard verification code is {code}. It expires in 15 minutes. If you did not request this, ignore this email."
    if settings.email_delivery_mode == "ses":
        import boto3
        from botocore.config import Config
        boto3.client("sesv2", region_name=settings.aws_region,
                     config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})).send_email(
            FromEmailAddress=settings.verification_sender,
            Destination={"ToAddresses": [email]},
            Content={"Simple": {"Subject": {"Data": "Verify your DataGuard email", "Charset": "UTF-8"},
                                "Body": {"Text": {"Data": text, "Charset": "UTF-8"}}}})
    elif settings.is_local:
        # Development-only mailbox, excluded from git and release packaging.
        path = settings.storage_root.parent / "verification-mail"
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{secrets.token_hex(12)}.json").write_text(json.dumps({"to": email, "body": text}), encoding="utf-8")
    else:
        raise RuntimeError("Local mail delivery is forbidden in AWS mode.")


def issue_code(user):
    now = datetime.now(timezone.utc)
    sent = user.verification_sent_at
    if sent and (now - sent.replace(tzinfo=timezone.utc)).total_seconds() < 60:
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    deliver_code(user.email, code)
    user.verification_digest = code_digest(user.id, code)
    user.verification_expires_at = now + timedelta(minutes=15)
    user.verification_sent_at = now
    user.verification_attempts = 0
    return True


def consume_code(user, code):
    now = datetime.now(timezone.utc)
    expiry = user.verification_expires_at
    if (not user.verification_digest or not expiry or
            expiry.replace(tzinfo=timezone.utc) <= now or user.verification_attempts >= 5):
        return False
    user.verification_attempts += 1
    if not hmac.compare_digest(user.verification_digest, code_digest(user.id, code)):
        return False
    user.email_verified_at = now
    user.verification_digest = None
    user.verification_expires_at = None
    return True

"""Deploy DataGuard from this checkout; requires an authenticated AWS CLI profile.

Run from the repo root with backend/.venv/Scripts/python.exe deploy/aws/deploy.py.
No SSH key, domain, Docker Desktop, or manually created AWS resources required.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from template import build_template

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "deploy" / ".state"


def log(message):
    print(message, flush=True)


def run_local(command, cwd, **kwargs):
    subprocess.run(command, cwd=cwd, check=True, **kwargs)


def build_frontend():
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run_local([npm, "ci"], ROOT / "Frontend")
    run_local([npm, "test"], ROOT / "Frontend")
    run_local([npm, "run", "build"], ROOT / "Frontend", env={**os.environ, "VITE_DATA_MODE": "api"})


def package_release():
    """An allowlist excludes .env, credentials, node_modules, runtime and .git."""
    paths = []
    for directory in ("backend/app", "backend/migrations", "Frontend/dist", "deploy/aws"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and not p.name.startswith(".env")
                     and p.suffix not in {".pyc", ".log", ".pem", ".key"})
    paths.extend(ROOT / name for name in ("backend/requirements.txt", "backend/alembic.ini"))
    if not (ROOT / "Frontend/dist/index.html").exists():
        raise RuntimeError("Frontend build is missing. Run without --skip-build.")
    # Verify the build selected API mode rather than accidentally packaging a
    # developer's demo build. Vite removes the literal VITE_DATA_MODE at build.
    manifest = ROOT / "Frontend/dist/deployment-mode.json"
    if not manifest.exists() or json.loads(manifest.read_text())["mode"] != "api":
        raise RuntimeError("Frontend is not a verified API build; run without --skip-build.")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(set(paths)):
            data = path.read_bytes()
            name = path.relative_to(ROOT).as_posix()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()[:16]


def ensure_secret(ssm, name, length=48):
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        value = secrets.token_urlsafe(length)
        ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=False)
        return value


def wait_stack(cf, stack):
    deadline = time.monotonic() + 3600
    previous = None
    while time.monotonic() < deadline:
        description = cf.describe_stacks(StackName=stack)["Stacks"][0]
        events = cf.describe_stack_events(StackName=stack)["StackEvents"]
        event = next((e for e in events if e["ResourceType"] != "AWS::CloudFormation::Stack"), events[0])
        summary = f"{description['StackStatus']}: {event['LogicalResourceId']} {event['ResourceStatus']}"
        if summary != previous:
            log(summary)
            previous = summary
        if description["StackStatus"] in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            return {item["OutputKey"]: item["OutputValue"] for item in description["Outputs"]}
        if not description["StackStatus"].endswith("IN_PROGRESS"):
            failures = [f"{e['LogicalResourceId']}: {e.get('ResourceStatusReason', '')}" for e in events
                        if e["ResourceStatus"].endswith("FAILED")]
            raise RuntimeError("CloudFormation failed: " + "; ".join(failures))
        time.sleep(15)
    raise TimeoutError("CloudFormation still running; inspect the stack before retrying.")


def provision(session, args):
    cf = session.client("cloudformation")
    template = json.dumps(build_template())
    # Validate server-side before making a stack change.
    cf.validate_template(TemplateBody=template)
    parameters = [
        {"ParameterKey": "OperatorEmail", "ParameterValue": args.operator_email},
        {"ParameterKey": "VerificationSender", "ParameterValue": args.verification_sender},
    ]
    try:
        existing = cf.describe_stacks(StackName=args.stack)["Stacks"][0]
    except ClientError as exc:
        if "does not exist" not in exc.response["Error"]["Message"]:
            raise
        existing = None
    if existing:
        if existing["StackStatus"].endswith("IN_PROGRESS"):
            return wait_stack(cf, args.stack)
        if existing["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}:
            raise RuntimeError(f"Existing stack is {existing['StackStatus']}; inspect and recover it before deploying.")
        change_set = "dataguard-" + secrets.token_hex(6)
        cf.create_change_set(StackName=args.stack, ChangeSetName=change_set,
            ChangeSetType="UPDATE", TemplateBody=template, Parameters=parameters, Capabilities=["CAPABILITY_IAM"])
        while True:
            change = cf.describe_change_set(StackName=args.stack, ChangeSetName=change_set)
            if change["Status"] in {"CREATE_COMPLETE", "FAILED"}:
                break
            time.sleep(3)
        if change["Status"] == "FAILED":
            cf.delete_change_set(StackName=args.stack, ChangeSetName=change_set)
            if "didn't contain changes" in change.get("StatusReason", ""):
                return {item["OutputKey"]: item["OutputValue"] for item in existing["Outputs"]}
            raise RuntimeError(change.get("StatusReason", "Change set failed"))
        for item in change.get("Changes", []):
            resource = item["ResourceChange"]
            log(f"Infrastructure: {resource['Action']} {resource['LogicalResourceId']}")
            if resource["LogicalResourceId"] in {"Database", "DataBucket"} and (
                resource.get("Replacement") in {"True", "Conditional"} or resource["Action"] == "Remove"
            ):
                cf.delete_change_set(StackName=args.stack, ChangeSetName=change_set)
                raise RuntimeError("Refusing automatic replacement/removal of application data resources.")
        cf.execute_change_set(StackName=args.stack, ChangeSetName=change_set)
    else:
        cf.create_stack(StackName=args.stack, TemplateBody=template, Parameters=parameters,
            Capabilities=["CAPABILITY_IAM"], EnableTerminationProtection=True, DisableRollback=True,
            Tags=[{"Key": "Application", "Value": "DataGuard"}, {"Key": "ManagedBy", "Value": "CloudFormation"}])
    return wait_stack(cf, args.stack)


def wait_ssm(ssm, instance):
    for _ in range(90):
        info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance]}])["InstanceInformationList"]
        if info and info[0]["PingStatus"] == "Online":
            return
        time.sleep(10)
    raise TimeoutError("EC2 did not register with SSM. Inspect cloud-init and instance status.")


def execute_remote(ssm, instance, commands, timeout=2400):
    command_id = ssm.send_command(InstanceIds=[instance], DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["#!/bin/bash\nset -euo pipefail\n" + "\n".join(commands)], "executionTimeout": [str(timeout)]},
        TimeoutSeconds=timeout, Comment="Install and verify the DataGuard release")["Command"]["CommandId"]
    log(f"SSM deployment command: {command_id}")
    printed = ""
    for _ in range(timeout // 5 + 30):
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(5)
            continue
        output = result.get("StandardOutputContent", "")
        if output != printed:
            if output.startswith(printed):
                log(output[len(printed):].rstrip())
            else:
                log(output[-2000:])
            printed = output
        if result["Status"] == "Success":
            return command_id
        if result["Status"] in {"Failed", "Cancelled", "TimedOut", "Cancelling"}:
            log(result.get("StandardErrorContent", "")[-6000:])
            raise RuntimeError(f"SSM deployment {result['Status']}; inspect command {command_id}.")
        time.sleep(5)
    raise TimeoutError(f"SSM command {command_id} has not completed; inspect before rerunning.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--stack", default="dataguard-prod")
    parser.add_argument("--operator-email", default="")
    parser.add_argument("--verification-sender", default="", help="Verified SES sender; enables required email verification")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,70}", args.stack):
        parser.error("Use a stack name of up to 71 letters, digits and hyphens.")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    # Preserve existing notification and verification settings on routine redeploys.
    try:
        previous = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
        previous_parameters = {p["ParameterKey"]: p.get("ParameterValue", "") for p in previous["Parameters"]}
        args.operator_email = args.operator_email or previous_parameters.get("OperatorEmail", "")
        args.verification_sender = args.verification_sender or previous_parameters.get("VerificationSender", "")
    except ClientError as exc:
        if "does not exist" not in exc.response["Error"]["Message"]:
            raise
    if args.verification_sender:
        identity = session.client("sesv2").get_email_identity(EmailIdentity=args.verification_sender)
        if not identity.get("VerifiedForSendingStatus"):
            raise RuntimeError("Verify the SES sender before enabling email verification.")
    log(f"Deploying {args.stack} in AWS account {account}, region {args.region}.")
    if not args.skip_build:
        build_frontend()
    archive, release_id = package_release()
    log(f"Prepared release {release_id}, {len(archive):,} bytes; secrets and local data excluded.")
    ssm = session.client("ssm", config=Config(retries={"mode": "standard", "max_attempts": 5}))
    prefix = f"/dataguard/{args.stack}"
    output = provision(session, args)
    session.client("logs").put_retention_policy(
        logGroupName=f"/aws/rds/instance/{output['DatabaseId']}/postgresql", retentionInDays=14
    )
    STATE.mkdir(parents=True, exist_ok=True)
    state_path = STATE / f"{args.stack}.json"
    previous_email_acceptance = "pending real inbox code confirmation"
    if state_path.exists():
        previous_email_acceptance = json.loads(state_path.read_text(encoding="utf-8")).get("features", {}).get("email_inbox_acceptance", previous_email_acceptance)
    state = {"account": account, "region": args.region, "stack": args.stack,
             "release": release_id, "status": "infrastructure-ready", "outputs": output}
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    settings = {"db_host": output["DatabaseHost"], "db_name": "dataguard", "db_user": "dataguard_app",
        "db_sslmode": "require", "storage_mode": "s3", "queue_mode": "sqs", "s3_bucket_name": output["DataBucket"],
        "sqs_queue_url": output["QueueUrl"], "sns_topic_arn": output["TopicArn"], "lease_seconds": "120",
        "public_url": output["WebsiteUrl"],
        "email_verification_required": "true" if args.verification_sender else "false",
        "email_delivery_mode": "ses", "verification_sender": args.verification_sender or "disabled"}
    for name, value in settings.items():
        ssm.put_parameter(Name=prefix + "/" + name, Value=value, Type="String", Overwrite=True)
    ensure_secret(ssm, prefix + "/db_password")
    ensure_secret(ssm, prefix + "/jwt_secret", 64)
    ensure_secret(ssm, prefix + "/ingestion_secret", 64)
    s3 = session.client("s3")
    bucket = output["ArtifactBucket"]
    key = f"releases/{release_id}.tar.gz"
    s3.put_object(Bucket=bucket, Key=key, Body=archive, ServerSideEncryption="AES256")
    # On first deploy the database application role must be created with the
    # RDS-managed admin credential. The instance never gets Secrets Manager
    # access; the encrypted bootstrap object is removed, including its version.
    bootstrap_key = f"bootstrap/{secrets.token_hex(16)}.json"
    admin = session.client("secretsmanager").get_secret_value(SecretId=output["DatabaseAdminSecretArn"])["SecretString"]
    bootstrap = s3.put_object(Bucket=bucket, Key=bootstrap_key, Body=admin.encode(), ServerSideEncryption="AES256")
    del admin
    try:
        log("Waiting for EC2 Systems Manager registration.")
        wait_ssm(ssm, output["InstanceId"])
        release_dir = f"/opt/dataguard/releases/{release_id}"
        commands = [f"install -d -m 755 {release_dir}",
            f"aws s3 cp {shlex.quote('s3://' + bucket + '/' + key)} {release_dir}/release.tar.gz --region {args.region} --only-show-errors",
            f"tar -xzf {release_dir}/release.tar.gz -C {release_dir}",
            "BOOTSTRAP_URI=" + shlex.quote(f"s3://{bucket}/{bootstrap_key}") + " bash " +
            shlex.quote(release_dir + "/deploy/aws/remote_install.sh") + " " +
            " ".join(shlex.quote(v) for v in (release_dir, args.region, prefix, output["LogGroup"], output["PublicIp"], output["TopicArn"]))]
        state["command_id"] = execute_remote(ssm, output["InstanceId"], commands)
    finally:
        s3.delete_object(Bucket=bucket, Key=bootstrap_key, VersionId=bootstrap["VersionId"])
        log("Temporary database bootstrap credential removed from S3.")
    state["status"] = "application-installed"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    # Verify a real anonymous signup, queued CSV processing, all four S3
    # downloads, report counts and resource ownership through public HTTPS.
    from verify import verify
    def controls(action):
        commands = {
            "stop-worker": ["systemctl stop dataguard-worker", "systemctl is-active dataguard-api"],
            "start-worker": ["systemctl start dataguard-worker"],
            "restart-services": ["systemctl restart dataguard-api dataguard-worker"],
            "check-notifications": ["sleep 8", "docker exec dataguard-api python -c " + shlex.quote(
                "from app.database import session_scope; from sqlalchemy import text; "
                "s=session_scope(); db=s.__enter__(); "
                "count=db.execute(text(\"SELECT count(*) FROM notification_events WHERE status='SENT'\")).scalar(); "
                "assert count > 0, 'No delivered SNS notification'; print('SNS outbox delivered:',count); s.__exit__(None,None,None)"
            )],
            "check-tls-renewal": ["systemctl is-enabled dataguard-tls-renew.timer",
                "systemctl is-active dataguard-tls-renew.timer",
                "docker run --rm -v /etc/letsencrypt:/etc/letsencrypt -v /var/lib/letsencrypt:/var/lib/letsencrypt "
                "certbot/certbot:v5.8.0 renew --dry-run --no-random-sleep-on-renew"],
        }
        return execute_remote(ssm, output["InstanceId"], commands[action], timeout=240)

    def verified_test_account():
        # Controlled synthetic fixture only. This does NOT claim to verify inbox delivery.
        # Passwords/tokens are never sent through SSM, logged, or saved to inventory.
        from pwdlib import PasswordHash
        email = f"deployment-{secrets.token_hex(8)}@example.com"
        password = secrets.token_urlsafe(24)
        hashed = PasswordHash.recommended().hash(password)
        code = ("from app.database import session_scope; from app.models import User; "
                "from datetime import datetime,timezone; import uuid; "
                "s=session_scope(); db=s.__enter__(); "
                f"db.add(User(id=uuid.uuid4(),email={email!r},password_hash={hashed!r},email_verified_at=datetime.now(timezone.utc))); "
                "s.__exit__(None,None,None); print('Created synthetic verified test fixture')")
        execute_remote(ssm, output["InstanceId"], ["docker exec dataguard-api python -c " + shlex.quote(code)], timeout=120)
        return email, password

    state["verification"] = verify(output["WebsiteUrl"], controls=controls,
        account_factory=verified_test_account if args.verification_sender else None)
    if args.verification_sender:
        state["verification"]["email_inbox_acceptance"] = "pending user verification"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        from verify_features import verify_features
        state["features"] = verify_features(output["WebsiteUrl"], session, output, verified_test_account)
        if previous_email_acceptance.startswith("passed:"):
            state["features"]["email_inbox_acceptance"] = previous_email_acceptance
            state["verification"]["email_inbox_acceptance"] = previous_email_acceptance
    state["status"] = "verified-with-limitations" if args.verification_sender else "verified"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(f"Application deployed and verified: {output['WebsiteUrl']}")
    log(f"Deployment inventory: {state_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"Deployment stopped: {type(exc).__name__}: {exc}")
        sys.exit(1)

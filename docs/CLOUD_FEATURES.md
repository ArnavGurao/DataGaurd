# Cloud feature additions

## Email verification

With `EMAIL_VERIFICATION_REQUIRED=true`, registration creates an unverified account and sends a six-digit code using SES. Enter the code in the signup screen, then sign in. Existing accounts must also verify their addresses; their files and rules remain intact. A failed login for an unverified account opens the verification screen, where **Send a new code** is available.

Codes expire after 15 minutes, are single-use, and are stored as a keyed digest. Five incorrect attempts invalidate a code. Resending is limited to once per minute per account; Nginx also limits authentication requests by IP. Local development can write messages to the gitignored `backend/runtime/verification-mail/` mailbox. That mode is prohibited in AWS.

The sender is an SES identity selected at deployment, not an SMTP password. SES sandbox accounts can only deliver to verified recipients (or SES test simulators). Sender verification alone does not enable arbitrary public signup delivery. Until SES production access is approved, verify each demo recipient in the SES console. Account creation stays unverified if delivery fails.

## Automatic S3 ingestion

Open **Upload dataset → Automatic S3 uploads**. Create a named folder and select a rule set. The app displays `s3://<data-bucket>/incoming/<source-id>/`. An authorized AWS operator uploads a CSV directly inside that folder using the S3 console or CLI:

```powershell
aws s3 cp samples/students_dirty.csv s3://YOUR_BUCKET/incoming/YOUR_SOURCE_ID/students.csv --region ap-south-1
```

S3 object-created events invoke Lambda. Lambda signs a small callback containing the bucket, key and exact version ID. FastAPI authenticates the signature, verifies the configured bucket and registered folder owner, reads that exact version, stores an immutable audit input, and creates a normal pending job. The existing publisher sends it to SQS and the worker validates it. Refresh dataset history to see the result.

The public API does not grant arbitrary S3 upload permissions. Folder mapping determines ownership and rules; S3 object metadata cannot override them. Each bucket/key/version combination creates at most one job. Overwriting a key creates a new version and a new audit. Database row locking serializes concurrent callbacks for a folder. Disabling a folder stops future imports without deleting earlier jobs.

Lambda failures retry and reach a dedicated ingestion dead-letter queue, monitored by CloudWatch. Non-CSV files are skipped. Invalid/oversized CSVs and disabled folders are recorded as ignored in Lambda logs. CSV limits remain 5 MiB, 20,000 rows and 50 columns. Outputs are outside `incoming/`, avoiding event loops.

## Deployment and verification

```powershell
backend/.venv/Scripts/python.exe deploy/aws/deploy.py --region ap-south-1 --verification-sender YOUR_VERIFIED_SENDER
```

Existing operator email and sender parameters are preserved when omitted on later deploys. Deployment adds a schema migration, IAM permissions, Lambda, a dead-letter queue, logging, an alarm, and an S3 notification. It preserves the data bucket and database.

Automated cloud acceptance uses explicitly provisioned synthetic verified fixtures for upload/isolation tests. These fixtures do not prove delivery to a real mailbox. The real email code flow must also be accepted using an inbox. The deployment inventory records email acceptance separately from successful infrastructure tests. The user has confirmed successful code receipt and verification for the approved demo recipient.

The optional live browser suite requires a verified synthetic account through `DATAGUARD_TEST_EMAIL` and `DATAGUARD_TEST_PASSWORD`; it no longer assumes arbitrary example.com signups can bypass verification. Keep those values private and out of source control. The local browser acceptance test mocks services to exercise verification, manual rule creation, and folder controls.

References: [SES sandbox](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html), [S3 and Lambda](https://docs.aws.amazon.com/lambda/latest/dg/with-s3.html).

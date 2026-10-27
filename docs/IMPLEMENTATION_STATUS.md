# DataGuard implementation status

Last updated: 27 October 2026.

## Current scope

- CSV validation with manual reusable rule sets, reports and private downloads.
- Email verification using Amazon SES.
- Automatic S3 ingestion using Lambda and the existing SQS/worker pipeline.

English rule generation and Amazon Bedrock were removed at the user's request. The English-rule button, backend draft endpoint, model service, configuration and Bedrock IAM permissions have been removed. Existing rule sets and job history remain available through the manual editor and dataset screens.

## Verification

- 151 backend tests passed.
- 10 frontend unit tests passed.
- Browser acceptance passed for signup verification, manual rule creation and ingestion-folder controls, including absence of the English-rule button.
- Production frontend build and CloudFormation lint passed.
- Release `7a5c31af420bb840` is deployed and verified. CloudFormation is `UPDATE_COMPLETE`. Live checks passed for ordinary uploads/downloads, account isolation, worker pause/recovery, restart persistence, SNS delivery, HTTPS renewal, S3/Lambda processing and duplicate notifications. The removed draft endpoint returns 404. Bedrock permissions and its Parameter Store setting were confirmed absent.

## Email delivery

SES sender `arnavagurao@gmail.com` and demo recipient `guraoarnav@gmail.com` are verified. The user confirmed successful code receipt and account verification. IAM permits sending with the configured sender through a `ses:FromAddress` condition.

SES is still in sandbox mode. Additional demo recipients must be verified in SES until production access is approved. This restriction is separate from the DataGuard account verification code.

## Where to use the features

- Rule sets → Create rule set.
- Signup/login → Verify email or Send a new code.
- Upload dataset → Automatic S3 uploads.

The live site is https://13.234.189.176. Full operating instructions are in [CLOUD_FEATURES.md](CLOUD_FEATURES.md). No source commit or push was made; pre-existing workspace changes were preserved.

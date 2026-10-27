# Live AWS deployment

Updated and verified on 27 October 2026. New feature details and limitations: [implementation status](IMPLEMENTATION_STATUS.md).

- **Website:** https://13.234.189.176
- **Region:** Mumbai (`ap-south-1`)
- **CloudFormation stack:** `dataguard-prod`
- **Stack status:** `UPDATE_COMPLETE`; 42 resources, including Lambda ingestion additions.
- **Application:** React production build, FastAPI API/publisher and separate worker.
- **Compute:** EC2 `t3.small`, Amazon Linux 2023, Docker and systemd, stable Elastic IP.
- **Database:** Private encrypted RDS PostgreSQL, separate application login, TLS,
  one-day backups and deletion protection.
- **Storage and processing:** Private S3, SQS jobs/DLQ, SNS operator notices.
- **Monitoring:** API/worker and RDS logs with 14-day retention; queue age, DLQ
  and instance health alarms.
- **Automatic ingestion:** S3 incoming folders trigger Lambda, which authenticates its callback and creates an account-owned validation job. Separate Lambda DLQ and failure alarm are installed. Live version-isolation and duplicate-notification tests passed.
- **Email verification:** Required at signup and for existing unverified accounts. SES sender `arnavagurao@gmail.com` is verified. Sandbox recipient restrictions still apply; the user confirmed successful inbox delivery and account verification.
- **Rule creation:** Manual rule-set editor; English/AI generation removed at the user's request.
- **HTTPS:** Trusted Let's Encrypt IP certificate; TLS 1.3 verified. Renewal
  timer checks every six hours and alerts on failure. Renewal dry run passed.
- **Budget:** Account-wide $25/month alert budget, with actual and forecast
  notifications. This is an alert threshold, not a spending cap.

Register an account at the website, create a rule set and upload a CSV. The live
build opens in API mode. Test accounts contain synthetic data only; there is
no shared application password.

## Verified workflows

| Check | Result |
|---|---|
| Registration and login through public HTTPS | Passed |
| Dirty CSV | 6 total, 2 valid, 4 rejected |
| Good CSV | 6 total, 6 valid, 0 rejected |
| Original/valid/rejected/report S3 downloads | Passed |
| Original bytes and exported row counts | Passed |
| Other-account job, report and download isolation | Passed |
| API dispatch with worker stopped | Passed |
| Worker restart drains queued work | Passed |
| API/worker restart preserves completed results | Passed |
| SNS publishing recorded as delivered in outbox | Passed |
| HTTPS certificate renewal simulation | Passed |
| Live Edge browser signup, rules, upload, report and download | Passed |
| Browser sign-in after reload preserves server-side history | Passed |
| Backend unit tests | 151 passed after removing English rule generation |
| Frontend unit tests | 10 passed |
| Local browser workflows | 5 passed |
| CloudFormation validation and lint | Passed |
| New feature browser flow with mocked services | Passed |
| Direct S3 upload through Lambda | Passed |
| Exact S3 versions and duplicate event handling | Passed |
| Unverified login and invalid-code rejection | Passed |
| English-rule endpoint and Bedrock integration removed | Passed; draft endpoint returns 404, IAM permission and model parameter removed |
| SES delivery from application IAM role | Passed; successful verification confirmed by user |

**Operator email:** SNS subscription was confirmed in the live account check on 27 October. SNS outbox publishing passed deployment verification. This is separate from SES signup verification.

Exact identifiers and machine-readable verification results are in the
gitignored `deploy/.state/dataguard-prod.json`. The source and operating guide
are in [deploy/aws/README.md](../deploy/aws/README.md). Failed deployment attempts
were removed; the account retains one DataGuard application instance and one
DataGuard database, alongside the account's pre-existing unrelated resources.

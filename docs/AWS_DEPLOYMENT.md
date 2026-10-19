# DataGuard AWS Deployment Guide

This guide deploys the existing React frontend and FastAPI backend on AWS using:

- EC2 for the API, worker, and Nginx frontend server
- RDS PostgreSQL for durable application data
- S3 for uploaded CSV files and generated reports
- SQS for background validation jobs, with a dead-letter queue
- SNS for operator notifications
- Systems Manager Parameter Store for secrets
- CloudWatch for logs and alarms

The commands below use PowerShell and AWS CLI v2. Run them from the repository root unless a command changes directory.

## 0. Important code checkpoint

Before creating AWS resources, finish the three cloud adapter implementations. These files currently contain `NotImplementedError` placeholders:

- `backend/app/services/storage.py`: implement `S3Storage.save_bytes`, `read_bytes`, `delete_object`, `exists`, and `get_download_url`.
- `backend/app/services/dispatcher.py`: implement `SqsJobQueue.send`, `receive`, and `delete`.
- `backend/app/services/notifier.py`: implement `SnsNotifier.send`.

Use the existing interfaces and comments in those files. Do not add AWS access keys to the repository. Boto3 must use the EC2 instance role.

The deployment cannot pass its end-to-end test until these methods are implemented.

## 1. Install tools and choose a region

Install AWS CLI v2, Git, Node.js, and Python 3.12 or newer. Configure a non-root AWS identity with MFA where possible.

```powershell
aws --version
aws sts get-caller-identity
aws configure
```

Choose one region and use it everywhere. The examples use `us-east-1`:

```powershell
$env:AWS_REGION = "us-east-1"
$env:AWS_DEFAULT_REGION = $env:AWS_REGION
$env:PROJECT = "dataguard"
$env:ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$env:VPC_CIDR = "10.20.0.0/16"
```

Never use the AWS root account for application access. Record the region, account ID, resource IDs, and estimated monthly cost in your project evidence.

## 2. Create the VPC and security groups

This student deployment uses one public subnet for EC2 and two private subnets for the RDS subnet group. It deliberately avoids a NAT Gateway to control cost.

```powershell
$vpcId = aws ec2 create-vpc --cidr-block $env:VPC_CIDR --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=$env:PROJECT-vpc}]" --query Vpc.VpcId --output text
aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-support '{"Value":true}'
aws ec2 modify-vpc-attribute --vpc-id $vpcId --enable-dns-hostnames '{"Value":true}'

$igwId = aws ec2 create-internet-gateway --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=$env:PROJECT-igw}]" --query InternetGateway.InternetGatewayId --output text
aws ec2 attach-internet-gateway --vpc-id $vpcId --internet-gateway-id $igwId

$azs = aws ec2 describe-availability-zones --state available --query "AvailabilityZones[0:2].ZoneName" --output text
$az = $azs -split "\s+"
$publicSubnetId = aws ec2 create-subnet --vpc-id $vpcId --cidr-block 10.20.1.0/24 --availability-zone $az[0] --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$env:PROJECT-public}]" --query Subnet.SubnetId --output text
$dbSubnetA = aws ec2 create-subnet --vpc-id $vpcId --cidr-block 10.20.10.0/24 --availability-zone $az[0] --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$env:PROJECT-db-a}]" --query Subnet.SubnetId --output text
$dbSubnetB = aws ec2 create-subnet --vpc-id $vpcId --cidr-block 10.20.11.0/24 --availability-zone $az[1] --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$env:PROJECT-db-b}]" --query Subnet.SubnetId --output text

$publicRouteTableId = aws ec2 create-route-table --vpc-id $vpcId --query RouteTable.RouteTableId --output text
aws ec2 create-route --route-table-id $publicRouteTableId --destination-cidr-block 0.0.0.0/0 --gateway-id $igwId
aws ec2 associate-route-table --route-table-id $publicRouteTableId --subnet-id $publicSubnetId
aws ec2 modify-subnet-attribute --subnet-id $publicSubnetId --map-public-ip-on-launch
```

Create security groups. Use your own IP for SSH, not `0.0.0.0/0`:

```powershell
$myIp = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
$ec2SgId = aws ec2 create-security-group --group-name "$env:PROJECT-ec2" --description "DataGuard EC2" --vpc-id $vpcId --query GroupId --output text
$rdsSgId = aws ec2 create-security-group --group-name "$env:PROJECT-rds" --description "DataGuard RDS" --vpc-id $vpcId --query GroupId --output text
aws ec2 authorize-security-group-ingress --group-id $ec2SgId --protocol tcp --port 22 --cidr "$myIp/32"
aws ec2 authorize-security-group-ingress --group-id $ec2SgId --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id $rdsSgId --protocol tcp --port 5432 --source-group $ec2SgId
```

Do not expose port 5432 publicly.

## 3. Create the RDS PostgreSQL database

Generate a database password locally and keep it private. A password manager is preferred. This creates a random 64-character value without writing it to a file:

```powershell
$dbPassword = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
```

Create the subnet group and database. Replace the instance class if your AWS lab has a different permitted class:

```powershell
aws rds create-db-subnet-group --db-subnet-group-name "$env:PROJECT-db-subnets" --db-subnet-group-description "DataGuard private database subnets" --subnet-ids $dbSubnetA $dbSubnetB
aws rds create-db-instance --db-instance-identifier "$env:PROJECT-db" --db-instance-class db.t4g.micro --engine postgres --engine-version 16 --allocated-storage 20 --storage-type gp3 --db-name dataguard --master-username dataguard_app --master-user-password $dbPassword --db-subnet-group-name "$env:PROJECT-db-subnets" --vpc-security-group-ids $rdsSgId --backup-retention-period 1 --no-publicly-accessible --storage-encrypted
aws rds wait db-instance-available --db-instance-identifier "$env:PROJECT-db"
$dbHost = aws rds describe-db-instances --db-instance-identifier "$env:PROJECT-db" --query "DBInstances[0].Endpoint.Address" --output text
```

RDS creation can take several minutes. Keep the RDS endpoint private; only EC2 should reach it.

## 4. Create S3, SQS, and SNS resources

S3 bucket names are globally unique. Add a random suffix:

```powershell
$suffix = Get-Random -Minimum 10000 -Maximum 99999
$bucket = "$env:PROJECT-inputs-$($env:ACCOUNT_ID)-$suffix"
aws s3api create-bucket --bucket $bucket --region $env:AWS_REGION
aws s3api put-public-access-block --bucket $bucket --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $bucket --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $bucket --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

$dlqUrl = aws sqs create-queue --queue-name "$env:PROJECT-jobs-dlq" --attributes MessageRetentionPeriod=1209600 --query QueueUrl --output text
$dlqArn = aws sqs get-queue-attributes --queue-url $dlqUrl --attribute-names QueueArn --query Attributes.QueueArn --output text
$redrive = "{\"deadLetterTargetArn\":\"$dlqArn\",\"maxReceiveCount\":\"3\"}"
$queueUrl = aws sqs create-queue --queue-name "$env:PROJECT-jobs" --attributes "VisibilityTimeout=180,ReceiveMessageWaitTimeSeconds=20,RedrivePolicy=$redrive" --query QueueUrl --output text

$topicArn = aws sns create-topic --name "$env:PROJECT-operators" --query TopicArn --output text
aws sns subscribe --topic-arn $topicArn --protocol email --notification-endpoint "your-email@example.com"
```

Confirm the SNS subscription from the email before testing notifications. SQS visibility timeout must be longer than the worker lease and the expected validation time.

## 5. Create the EC2 IAM role

Create an instance profile for EC2. Save this as a temporary file named `ec2-trust-policy.json`; do not commit it:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
```

Create `ec2-dataguard-policy.json`. Replace the placeholders before using it:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["ssm:GetParametersByPath"],"Resource":"arn:aws:ssm:REGION:ACCOUNT_ID:parameter/dataguard/*"},
    {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:DeleteObject","s3:HeadObject"],"Resource":"arn:aws:s3:::BUCKET/*"},
    {"Effect":"Allow","Action":["sqs:SendMessage","sqs:ReceiveMessage","sqs:DeleteMessage","sqs:ChangeMessageVisibility","sqs:GetQueueAttributes"],"Resource":["QUEUE_ARN","DLQ_ARN"]},
    {"Effect":"Allow","Action":["sns:Publish"],"Resource":"TOPIC_ARN"}
  ]
}
```

Create the role and attach the policy:

```powershell
aws iam create-role --role-name "$env:PROJECT-ec2-role" --assume-role-policy-document file://ec2-trust-policy.json
aws iam put-role-policy --role-name "$env:PROJECT-ec2-role" --policy-name "$env:PROJECT-runtime" --policy-document file://ec2-dataguard-policy.json
aws iam create-instance-profile --instance-profile-name "$env:PROJECT-ec2-profile"
aws iam add-role-to-instance-profile --instance-profile-name "$env:PROJECT-ec2-profile" --role-name "$env:PROJECT-ec2-role"
```

The role needs no S3 bucket-wide access, no `iam:*`, and no access keys.

## 6. Store runtime settings in Parameter Store

The application reads parameters below `/dataguard` when `APP_ENV=aws`. Parameter names are converted to environment variable names by their final path segment.

```powershell
function Put-Parameter($name, $value, $type = "SecureString") {
  aws ssm put-parameter --name "/dataguard/$name" --type $type --value $value --overwrite | Out-Null
}

$jwtSecret = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
Put-Parameter "db_password" $dbPassword
Put-Parameter "jwt_secret" $jwtSecret
Put-Parameter "db_host" $dbHost "String"
Put-Parameter "db_port" "5432" "String"
Put-Parameter "db_name" "dataguard" "String"
Put-Parameter "db_user" "dataguard_app" "String"
Put-Parameter "db_sslmode" "require" "String"
Put-Parameter "s3_bucket_name" $bucket "String"
Put-Parameter "sqs_queue_url" $queueUrl "String"
Put-Parameter "sns_topic_arn" $topicArn "String"
```

The EC2 service environment must also contain:

```text
APP_ENV=aws
AWS_REGION=us-east-1
SSM_PARAMETER_PREFIX=/dataguard
STORAGE_MODE=s3
QUEUE_MODE=sqs
```

## 7. Launch EC2

Launch an Ubuntu 24.04 LTS instance in the public subnet with the EC2 security group and instance profile. Use the AWS console if you do not already have an AMI ID and key pair. Recommended student-project settings:

- Instance type: `t3.micro` or the smallest permitted type
- Root volume: 20 GB gp3
- Auto-assign public IPv4: enabled
- IAM instance profile: `dataguard-ec2-profile`
- Security group: `dataguard-ec2`
- No public SSH access except your current IP

Connect after launch:

```powershell
ssh -i .\YOUR_KEY.pem ubuntu@EC2_PUBLIC_IP
```

On EC2:

```bash
sudo apt update
sudo apt install -y python3.12-venv python3-pip nginx git nodejs npm
sudo git clone YOUR_PRIVATE_REPOSITORY_URL /opt/dataguard
sudo chown -R ubuntu:ubuntu /opt/dataguard
cd /opt/dataguard/backend
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 8. Run migrations and seed the database

On EC2, set cloud mode before running Alembic. Secrets and resource names come from SSM through the instance role:

```bash
cd /opt/dataguard/backend
export APP_ENV=aws
export AWS_REGION=us-east-1
export SSM_PARAMETER_PREFIX=/dataguard
export STORAGE_MODE=s3
export QUEUE_MODE=sqs
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seed --email demo@example.com --password 'CHANGE_THIS_DEMO_PASSWORD'
```

Change the demo password before presenting the system. The seed command is optional if you will register through the UI.

## 9. Create systemd services

Create `/etc/dataguard.env`:

```text
APP_ENV=aws
AWS_REGION=us-east-1
SSM_PARAMETER_PREFIX=/dataguard
STORAGE_MODE=s3
QUEUE_MODE=sqs
```

Protect it:

```bash
sudo chmod 600 /etc/dataguard.env
```

Create `/etc/systemd/system/dataguard-api.service`:

```ini
[Unit]
Description=DataGuard FastAPI API
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/dataguard/backend
EnvironmentFile=/etc/dataguard.env
ExecStart=/opt/dataguard/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/dataguard-worker.service`:

```ini
[Unit]
Description=DataGuard validation worker
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/dataguard/backend
EnvironmentFile=/etc/dataguard.env
ExecStart=/opt/dataguard/backend/.venv/bin/python -m app.worker
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable both:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dataguard-api dataguard-worker
sudo systemctl status dataguard-api dataguard-worker
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/ready
```

## 10. Build and serve the frontend

The frontend uses same-origin `/api` requests, so Nginx must proxy `/api` to the API process.

```bash
cd /opt/dataguard/Frontend
npm ci
npm run build
sudo mkdir -p /var/www/dataguard
sudo cp -r dist/. /var/www/dataguard/
```

Create `/etc/nginx/sites-available/dataguard`:

```nginx
server {
    listen 80 default_server;
    server_name _;
    root /var/www/dataguard;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Enable it:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/dataguard /etc/nginx/sites-enabled/dataguard
sudo nginx -t
sudo systemctl reload nginx
```

Open `http://EC2_PUBLIC_IP/` in a browser. Do not use the Vite development server in production.

## 11. Verify the complete AWS workflow

Run these checks in order:

1. `http://EC2_PUBLIC_IP/api/health` returns the API health response.
2. `http://EC2_PUBLIC_IP/api/ready` returns database readiness.
3. Register or log in through the frontend.
4. Create or select a rule set.
5. Upload `samples/students_dirty.csv`.
6. Confirm the job moves through `PENDING_DISPATCH`, `QUEUED`, `RUNNING`, and `COMPLETED`.
7. Confirm objects exist in S3 under `uploads/` and `outputs/`.
8. Download the original, valid CSV, rejected CSV, and JSON report.
9. Confirm a rejected-row job produces an SNS email.
10. Stop the worker with `sudo systemctl stop dataguard-worker`, upload another file, restart it, and confirm the queued job completes.

Useful diagnostics on EC2:

```bash
sudo journalctl -u dataguard-api -n 100 --no-pager
sudo journalctl -u dataguard-worker -n 100 --no-pager
sudo journalctl -u nginx -n 50 --no-pager
```

Never include JWTs, database passwords, presigned URLs, or full CSV contents in screenshots or submitted logs.

## 12. Monitoring and cost controls

At minimum:

- Create a CloudWatch alarm for EC2 status check failure.
- Create an alarm for the SQS `ApproximateNumberOfMessagesVisible` metric.
- Create an AWS Budget with an email threshold appropriate to your credits.
- Record EC2, RDS, S3, SQS, SNS, CloudWatch, and public IPv4 resources.

For a short demo, stop EC2 and RDS when they are not needed. Stopping is not the same as deleting and may still incur storage charges.

## 13. Cleanup after the demonstration

Export screenshots and evidence first. Then delete in this order:

1. Delete the Nginx/API/worker host or terminate EC2.
2. Delete RDS, with a final snapshot only if required.
3. Empty and delete the S3 bucket.
4. Delete the SQS queues and SNS topic.
5. Delete the SSM parameters.
6. Remove the IAM inline policy, instance profile, and role.
7. Delete security groups, subnets, route tables, the internet gateway, and VPC.
8. Delete the budget only if it is no longer needed.

Keep a written cleanup record and verify in the AWS console that no unintended billable resources remain.

## Deployment completion checklist

- [ ] S3, SQS, and SNS adapter placeholders are implemented and tested.
- [ ] AWS CLI identity, region, and budget are configured.
- [ ] Custom VPC has one public EC2 subnet and two private RDS subnets.
- [ ] RDS is private and accepts PostgreSQL only from the EC2 security group.
- [ ] S3 public access is blocked and encryption is enabled.
- [ ] SQS has a 3-attempt dead-letter queue policy.
- [ ] EC2 uses an IAM role; no access keys are stored on disk or in Git.
- [ ] Secrets and cloud resource names are in Parameter Store.
- [ ] Alembic migrations ran successfully.
- [ ] API and worker run as separate systemd services.
- [ ] Nginx serves the built frontend and proxies `/api/`.
- [ ] A dirty CSV completed and produced all four artifacts.
- [ ] SNS notification, queue recovery, health checks, and alarms were demonstrated.

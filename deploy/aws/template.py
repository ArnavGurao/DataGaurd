"""Generate the reviewable CloudFormation template (no credentials required)."""
from __future__ import annotations

import json
from pathlib import Path


def ref(name):
    return {"Ref": name}


def attr(name, field):
    return {"Fn::GetAtt": [name, field]}


def sub(value):
    return {"Fn::Sub": value}


def build_template():
    resources = {}

    def add(name, kind, properties, **extra):
        resources[name] = {"Type": f"AWS::{kind}", "Properties": properties, **extra}

    add("Vpc", "EC2::VPC", {"CidrBlock": "10.42.0.0/16", "EnableDnsSupport": True,
        "EnableDnsHostnames": True, "Tags": [{"Key": "Name", "Value": sub("${AWS::StackName}-vpc")}]})
    add("InternetGateway", "EC2::InternetGateway", {})
    add("GatewayAttachment", "EC2::VPCGatewayAttachment", {"VpcId": ref("Vpc"), "InternetGatewayId": ref("InternetGateway")})
    for name, cidr, zone, public in [("AppSubnet", "10.42.1.0/24", 0, True),
                                     ("DbSubnetA", "10.42.10.0/24", 0, False),
                                     ("DbSubnetB", "10.42.11.0/24", 1, False)]:
        add(name, "EC2::Subnet", {"VpcId": ref("Vpc"), "CidrBlock": cidr,
            "AvailabilityZone": {"Fn::Select": [zone, {"Fn::GetAZs": ""}]}, "MapPublicIpOnLaunch": public})
    add("PublicRoutes", "EC2::RouteTable", {"VpcId": ref("Vpc")})
    add("InternetRoute", "EC2::Route", {"RouteTableId": ref("PublicRoutes"), "DestinationCidrBlock": "0.0.0.0/0",
        "GatewayId": ref("InternetGateway")}, DependsOn="GatewayAttachment")
    add("AppRouteAssociation", "EC2::SubnetRouteTableAssociation", {"RouteTableId": ref("PublicRoutes"), "SubnetId": ref("AppSubnet")})
    add("PrivateRoutes", "EC2::RouteTable", {"VpcId": ref("Vpc")})
    for name in ("DbSubnetA", "DbSubnetB"):
        add(name + "Routes", "EC2::SubnetRouteTableAssociation", {"RouteTableId": ref("PrivateRoutes"), "SubnetId": ref(name)})
    add("AppSecurityGroup", "EC2::SecurityGroup", {"VpcId": ref("Vpc"), "GroupDescription": "Public HTTPS and ACME validation; no SSH",
        "SecurityGroupIngress": [{"IpProtocol": "tcp", "FromPort": port, "ToPort": port,
            "CidrIp": "0.0.0.0/0"} for port in (80, 443)],
        "SecurityGroupEgress": [{"IpProtocol": "-1", "CidrIp": "0.0.0.0/0"}]})
    add("DbSecurityGroup", "EC2::SecurityGroup", {"VpcId": ref("Vpc"), "GroupDescription": "Postgres from the app only",
        "SecurityGroupIngress": [{"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
            "SourceSecurityGroupId": ref("AppSecurityGroup")}]})
    add("DbSubnets", "RDS::DBSubnetGroup", {"DBSubnetGroupDescription": "Private DataGuard database subnets",
        "SubnetIds": [ref("DbSubnetA"), ref("DbSubnetB")]})
    add("Database", "RDS::DBInstance", {"Engine": "postgres", "DBInstanceClass": ref("DatabaseClass"),
        "AllocatedStorage": "20", "MaxAllocatedStorage": 30, "StorageType": "gp3", "StorageEncrypted": True,
        "DBName": "dataguard", "MasterUsername": "dataguard_admin", "ManageMasterUserPassword": True,
        "DBSubnetGroupName": ref("DbSubnets"), "VPCSecurityGroups": [ref("DbSecurityGroup")],
        "PubliclyAccessible": False, "MultiAZ": False, "BackupRetentionPeriod": 1,
        "DeletionProtection": True, "AutoMinorVersionUpgrade": True, "CopyTagsToSnapshot": True,
        "EnableCloudwatchLogsExports": ["postgresql"]}, DeletionPolicy="Snapshot", UpdateReplacePolicy="Snapshot")
    for name in ("DataBucket", "ArtifactBucket"):
        properties = {"PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
            "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
            "VersioningConfiguration": {"Status": "Enabled"}}
        if name == "ArtifactBucket":
            properties["LifecycleConfiguration"] = {"Rules": [
                {"Id": "ExpireBootstrap", "Status": "Enabled", "Prefix": "bootstrap/", "ExpirationInDays": 1,
                 "NoncurrentVersionExpiration": {"NoncurrentDays": 1}},
                {"Id": "ExpireOldReleases", "Status": "Enabled", "Prefix": "releases/", "ExpirationInDays": 30,
                 "NoncurrentVersionExpiration": {"NoncurrentDays": 1}}]}
        extra = {}
        if name == "DataBucket":
            properties["NotificationConfiguration"] = {"LambdaConfigurations": [{
                "Event": "s3:ObjectCreated:*", "Function": attr("IngestionFunction", "Arn"),
                "Filter": {"S3Key": {"Rules": [{"Name": "prefix", "Value": "incoming/"}]}}
            }]}
            extra["DependsOn"] = "IngestionInvokePermission"
        add(name, "S3::Bucket", properties, DeletionPolicy="Retain", UpdateReplacePolicy="Retain", **extra)
        add(name + "Policy", "S3::BucketPolicy", {"Bucket": ref(name), "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [attr(name, "Arn"), sub("${" + name + ".Arn}/*")],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}})
    add("DeadLetterQueue", "SQS::Queue", {"MessageRetentionPeriod": 1209600, "SqsManagedSseEnabled": True})
    add("JobsQueue", "SQS::Queue", {"ReceiveMessageWaitTimeSeconds": 20, "VisibilityTimeout": 180,
        "MessageRetentionPeriod": 345600, "SqsManagedSseEnabled": True,
        "RedrivePolicy": {"deadLetterTargetArn": attr("DeadLetterQueue", "Arn"), "maxReceiveCount": 5}})
    add("OperatorsTopic", "SNS::Topic", {})
    add("OperatorsTopicPolicy", "SNS::TopicPolicy", {"Topics": [ref("OperatorsTopic")], "PolicyDocument": {
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "cloudwatch.amazonaws.com"},
            "Action": "sns:Publish", "Resource": ref("OperatorsTopic"),
            "Condition": {"StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")}}}]}})
    add("OperatorEmailSubscription", "SNS::Subscription", {"Protocol": "email", "Endpoint": ref("OperatorEmail"),
        "TopicArn": ref("OperatorsTopic")}, Condition="HasOperatorEmail")
    add("MonthlyBudget", "Budgets::Budget", {
        "Budget": {"BudgetName": sub("${AWS::StackName}-monthly"), "BudgetType": "COST", "TimeUnit": "MONTHLY",
                   "BudgetLimit": {"Amount": 25, "Unit": "USD"}},
        "NotificationsWithSubscribers": [{
            "Notification": {"NotificationType": kind, "ComparisonOperator": "GREATER_THAN",
                             "Threshold": threshold, "ThresholdType": "PERCENTAGE"},
            "Subscribers": [{"SubscriptionType": "EMAIL", "Address": ref("OperatorEmail")}],
        } for kind, threshold in [("ACTUAL", 80), ("ACTUAL", 100), ("FORECASTED", 100)]]}, Condition="HasOperatorEmail")
    add("ApplicationLogs", "Logs::LogGroup", {"LogGroupName": sub("/dataguard/${AWS::StackName}/application"),
        "RetentionInDays": 14}, DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    statements = [
        {"Effect": "Allow", "Action": "s3:GetObjectVersion", "Resource": sub("${DataBucket.Arn}/incoming/*")},
        {"Effect": "Allow", "Action": "ses:SendEmail",
         "Resource": sub("arn:${AWS::Partition}:ses:${AWS::Region}:${AWS::AccountId}:identity/*"),
         "Condition": {"StringEquals": {"ses:FromAddress": ref("VerificationSender")}}},
        {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": attr("DataBucket", "Arn")},
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": [sub("${DataBucket.Arn}/uploads/*"), sub("${DataBucket.Arn}/outputs/*")]},
        {"Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": [sub("${ArtifactBucket.Arn}/releases/*"), sub("${ArtifactBucket.Arn}/bootstrap/*")]},
        {"Effect": "Allow", "Action": ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
         "Resource": attr("JobsQueue", "Arn")},
        {"Effect": "Allow", "Action": "sns:Publish", "Resource": ref("OperatorsTopic")},
        {"Effect": "Allow", "Action": ["ssm:GetParametersByPath", "ssm:GetParameter"],
         "Resource": [sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/dataguard/${AWS::StackName}"),
                      sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/dataguard/${AWS::StackName}/*")]},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": attr("ApplicationLogs", "Arn")},
        {"Effect": "Allow", "Action": ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
            "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["ec2messages:AcknowledgeMessage", "ec2messages:DeleteMessage",
            "ec2messages:FailMessage", "ec2messages:GetEndpoint", "ec2messages:GetMessages", "ec2messages:SendReply"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["ssm:DescribeAssociation", "ssm:GetDeployablePatchSnapshotForInstance",
            "ssm:GetDocument", "ssm:DescribeDocument", "ssm:GetManifest", "ssm:ListAssociations",
            "ssm:ListInstanceAssociations", "ssm:PutInventory", "ssm:PutComplianceItems", "ssm:PutConfigurePackageResult",
            "ssm:UpdateAssociationStatus", "ssm:UpdateInstanceAssociationStatus", "ssm:UpdateInstanceInformation"], "Resource": "*"},
    ]
    add("InstanceRole", "IAM::Role", {"AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "DataGuardResources", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]})
    add("InstanceProfile", "IAM::InstanceProfile", {"Roles": [ref("InstanceRole")]})
    add("IngestionDeadLetterQueue", "SQS::Queue", {"MessageRetentionPeriod": 1209600, "SqsManagedSseEnabled": True})
    add("IngestionLogs", "Logs::LogGroup", {"LogGroupName": sub("/aws/lambda/${AWS::StackName}-ingestion"), "RetentionInDays": 14})
    add("IngestionRole", "IAM::Role", {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "IngestionCallback", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": [sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/dataguard/${AWS::StackName}/" + name) for name in ("ingestion_secret", "public_url")]},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": attr("IngestionLogs", "Arn")},
            {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": attr("IngestionDeadLetterQueue", "Arn")}
        ]}}]})
    add("IngestionFunction", "Lambda::Function", {
        "FunctionName": sub("${AWS::StackName}-ingestion"), "Runtime": "python3.13", "Handler": "index.handler",
        "Role": attr("IngestionRole", "Arn"), "Timeout": 60, "MemorySize": 128,
        "Code": {"ZipFile": Path(__file__).with_name("ingestion_lambda.py").read_text(encoding="utf-8")},
        "Environment": {"Variables": {"PARAMETER_PREFIX": sub("/dataguard/${AWS::StackName}")}},
        "DeadLetterConfig": {"TargetArn": attr("IngestionDeadLetterQueue", "Arn")}})
    # SourceAccount avoids a bucket/permission dependency cycle. The callback additionally
    # requires the configured bucket, registered folder, version ID and HMAC signature.
    add("IngestionInvokePermission", "Lambda::Permission", {"FunctionName": ref("IngestionFunction"),
        "Action": "lambda:InvokeFunction", "Principal": "s3.amazonaws.com", "SourceAccount": ref("AWS::AccountId")})
    add("IngestionRetryPolicy", "Lambda::EventInvokeConfig", {"FunctionName": ref("IngestionFunction"),
        "Qualifier": "$LATEST", "MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 2})
    add("IngestionFailureAlarm", "CloudWatch::Alarm", {"Namespace": "AWS/SQS", "MetricName": "ApproximateNumberOfMessagesVisible",
        "Dimensions": [{"Name": "QueueName", "Value": attr("IngestionDeadLetterQueue", "QueueName")}],
        "Statistic": "Maximum", "Period": 60, "EvaluationPeriods": 1, "Threshold": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching", "AlarmActions": [ref("OperatorsTopic")]})
    # No application secrets in EC2 user data. SSM performs the release install.
    bootstrap = """#!/bin/bash
set -euo pipefail
dnf install -y docker nginx
systemctl enable --now docker amazon-ssm-agent
"""
    add("ApplicationInstance", "EC2::Instance", {"ImageId": ref("AmazonLinuxImage"), "InstanceType": ref("InstanceType"),
        "IamInstanceProfile": ref("InstanceProfile"), "SubnetId": ref("AppSubnet"),
        "SecurityGroupIds": [ref("AppSecurityGroup")], "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1},
        "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "Encrypted": True}}],
        "UserData": {"Fn::Base64": bootstrap},
        "Tags": [{"Key": "Name", "Value": sub("${AWS::StackName}-app")}]}, DependsOn=["InternetRoute", "AppRouteAssociation"])
    add("ApplicationIp", "EC2::EIP", {"Domain": "vpc"}, DependsOn="GatewayAttachment")
    add("IpAssociation", "EC2::EIPAssociation", {"AllocationId": attr("ApplicationIp", "AllocationId"), "InstanceId": ref("ApplicationInstance")})
    for name, queue, metric, threshold in [("QueueAgeAlarm", "JobsQueue", "ApproximateAgeOfOldestMessage", 300),
                                           ("DeadLetterAlarm", "DeadLetterQueue", "ApproximateNumberOfMessagesVisible", 1)]:
        add(name, "CloudWatch::Alarm", {"AlarmDescription": name, "Namespace": "AWS/SQS", "MetricName": metric,
            "Dimensions": [{"Name": "QueueName", "Value": attr(queue, "QueueName")}], "Statistic": "Maximum",
            "Period": 60, "EvaluationPeriods": 2, "Threshold": threshold, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching", "AlarmActions": [ref("OperatorsTopic")]})
    add("InstanceHealthAlarm", "CloudWatch::Alarm", {"Namespace": "AWS/EC2", "MetricName": "StatusCheckFailed",
        "Dimensions": [{"Name": "InstanceId", "Value": ref("ApplicationInstance")}], "Statistic": "Maximum", "Period": 300,
        "EvaluationPeriods": 2, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "TreatMissingData": "missing", "AlarmActions": [ref("OperatorsTopic")]})
    outputs = {
        "IngestionFunction": ref("IngestionFunction"), "IngestionDeadLetterQueueUrl": ref("IngestionDeadLetterQueue"),
        "WebsiteUrl": sub("https://${ApplicationIp}"),
        "InstanceId": ref("ApplicationInstance"), "PublicIp": ref("ApplicationIp"),
        "DatabaseHost": attr("Database", "Endpoint.Address"), "DatabaseId": ref("Database"),
        "DatabaseAdminSecretArn": attr("Database", "MasterUserSecret.SecretArn"),
        "DataBucket": ref("DataBucket"), "ArtifactBucket": ref("ArtifactBucket"), "QueueUrl": ref("JobsQueue"),
        "DeadLetterQueueUrl": ref("DeadLetterQueue"), "TopicArn": ref("OperatorsTopic"),
        "LogGroup": ref("ApplicationLogs"), "ParameterPrefix": sub("/dataguard/${AWS::StackName}"),
    }
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "DataGuard complete application: EC2, private RDS, S3, SQS, SNS, HTTPS and monitoring",
        "Parameters": {
            "AmazonLinuxImage": {"Type": "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>", "Default": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"},
            "InstanceType": {"Type": "String", "Default": "t3.small", "AllowedValues": ["t3.small", "t3.medium"]},
            "DatabaseClass": {"Type": "String", "Default": "db.t4g.micro", "AllowedValues": ["db.t4g.micro", "db.t4g.small"]},
            "OperatorEmail": {"Type": "String", "Default": ""},
            "VerificationSender": {"Type": "String", "Default": "", "Description": "Existing verified SES email identity"}},
        "Conditions": {"HasOperatorEmail": {"Fn::Not": [{"Fn::Equals": [ref("OperatorEmail"), ""]}]}},
        "Resources": resources, "Outputs": {name: {"Value": value} for name, value in outputs.items()}}


if __name__ == "__main__":
    path = Path(__file__).with_name("cloudformation.json")
    path.write_text(json.dumps(build_template(), indent=2) + "\n", encoding="utf-8")
    print(path)

# AWS Architecture Document: EPUB Translation Service (Ultra-Cost-Optimized)

## Executive Summary

This document outlines a lean, ultra-cost-optimized AWS architecture for deploying the EPUB Translation Service, specifically designed for very low initial usage (~10 translations/month) with ability to scale to ~1000/month at high growth.

**Key Optimizations:**
- **67% cost reduction**: $55/month vs $166/month traditional architecture
- **Scale-to-zero compute**: ECS tasks run only when needed (~2 hours/month)
- **Single AZ deployment**: 50% savings on NAT Gateway costs
- **Aggressive lifecycle policies**: 3-7 day file retention for Kindle email feature
- **Simplified monitoring**: Basic logging sufficient for low usage patterns

The service provides synchronous translation of EPUB files via a FastAPI-based REST API, supporting multiple translation models (OpenAI, Claude, Gemini, DeepL) with secure API key management in AWS Secrets Manager and temporary file storage optimized for the upcoming premium Kindle email delivery feature.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Service Components](#service-components)
3. [Network Architecture](#network-architecture)
4. [Security Design](#security-design)
5. [Cost Optimization](#cost-optimization)
6. [Deployment Strategy](#deployment-strategy)
7. [Monitoring & Observability](#monitoring--observability)
8. [Disaster Recovery](#disaster-recovery)
9. [Terraform Implementation](#terraform-implementation)

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                           Internet                               │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                CloudFront CDN                                   │
│          (Optional - for file downloads)                        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              Application Load Balancer                          │
│                    (ALB)                                        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                    VPC                                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Public Subnets                             │    │
│  │         (Multi-AZ: us-west-2a, us-west-2b)            │    │
│  └─────────────────────┬───────────────────────────────────┘    │
│                        │                                        │
│  ┌─────────────────────▼───────────────────────────────────┐    │
│  │             Private Subnets                             │    │
│  │         (Multi-AZ: us-west-2a, us-west-2b)            │    │
│  │                                                         │    │
│  │   ┌──────────────┐    ┌──────────────┐                │    │
│  │   │ ECS Fargate  │    │ ECS Fargate  │                │    │
│  │   │   Task 1     │    │   Task 2     │                │    │
│  │   │              │    │              │                │    │
│  │   │ EPUB Trans.  │    │ EPUB Trans.  │                │    │
│  │   │   Service    │    │   Service    │                │    │
│  │   └──────────────┘    └──────────────┘                │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    S3 Storage                                   │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │    Uploads      │  │    Results      │  │    Backups      │  │
│  │   Bucket        │  │   Bucket        │  │   Bucket        │  │
│  │                 │  │                 │  │                 │  │
│  │ Lifecycle: 7d   │  │ Lifecycle: 30d  │  │ Lifecycle: 1y   │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                Supporting Services                              │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ AWS Secrets │  │ CloudWatch  │  │  Parameter  │            │
│  │  Manager    │  │   Logs      │  │   Store     │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

### Architecture Decisions

**Compute Choice: ECS Fargate**
- **Selected**: ECS Fargate over EC2/Lambda
- **Rationale**:
  - Synchronous processing (1-10 minutes) exceeds Lambda timeout limits
  - No server management overhead compared to EC2
  - Pay-per-use model for sporadic workloads
  - Easy auto-scaling and container orchestration
  - Cost-effective for variable workloads

**Storage Strategy: S3 with Intelligent Tiering**
- Multi-bucket approach for lifecycle management
- Presigned URLs for secure file access
- CloudFront integration for global distribution

---

## Updated Requirements Analysis

### Usage Pattern Optimization
- **Initial Usage**: ~10 translations/month (ultra-low volume)
- **Growth Target**: ~1000 translations/month (high growth scenario)
- **Activity Pattern**: Sporadic usage with long idle periods
- **Cost Sensitivity**: High priority on cost optimization

### Kindle Email Integration (Premium Feature)
- **File Retention**: 3-7 days for email delivery
- **Storage Requirements**: Temporary storage for processed EPUBs
- **Email Service**: Future integration with SES for automated delivery
- **Premium Users**: Charged for advanced translation models

### Regional Deployment
- **Primary Region**: us-west-2 (user preference)
- **Availability**: Single AZ sufficient (cost optimization)
- **Backup Strategy**: Code in Git, no data replication needed

---

## Service Components

### 1. Compute Layer - ECS Fargate

**ECS Cluster Configuration:**
```yaml
Service: epub-translator-service
Cluster: epub-translator-cluster
Launch Type: Fargate
Platform Version: 1.4.0

Task Definition:
  CPU: 512 (0.5 vCPU)  # Right-sized for low usage
  Memory: 1024 MB (1 GB)
  Network Mode: awsvpc

Container Configuration:
  Image: {account-id}.dkr.ecr.us-west-2.amazonaws.com/epub-translator:latest
  Port: 8000
  Environment Variables:
    - STORAGE_MODE=s3
    - S3_BUCKET=epub-translator-files
    - AWS_REGION=us-west-2
    - LOG_LEVEL=INFO
    - KINDLE_EMAIL_ENABLED=true
    - FILE_RETENTION_DAYS=7
```

**Auto Scaling Configuration:**
```yaml
Target Tracking Scaling:
  - Metric: CPU Utilization
    Target: 70%
    Scale Out Cooldown: 300s
    Scale In Cooldown: 300s

  - Metric: Memory Utilization
    Target: 80%
    Scale Out Cooldown: 300s
    Scale In Cooldown: 300s

Capacity:
  Minimum: 1 task
  Maximum: 10 tasks
  Desired: 2 tasks
```

### 2. Storage Layer - Amazon S3

**Bucket Strategy:**
```yaml
Primary Storage Bucket: epub-translator-files
├── uploads/{job_id}/
│   └── original.epub
├── results/{job_id}/
│   └── translated.epub
└── temp/{job_id}/
    └── processing files

Backup Bucket: epub-translator-backups
├── daily-snapshots/
└── configuration-backups/

Lifecycle Policies:
  uploads/: Delete after 7 days
  results/: Transition to IA after 30 days, Glacier after 90 days
  temp/: Delete after 1 day
```

**S3 Security Configuration:**
```yaml
Bucket Policy:
  - Allow ECS Task Role read/write access
  - Deny public access
  - Enable versioning for results bucket
  - Enable server-side encryption (AES-256)

CORS Configuration:
  - Allow Origins: Application domains
  - Allow Methods: GET, POST, PUT
  - Allow Headers: Content-Type, Authorization
```

### 3. Load Balancing - Application Load Balancer

**ALB Configuration:**
```yaml
Load Balancer: epub-translator-alb
Type: Application Load Balancer
Scheme: Internet-facing
Security Groups:
  - Allow HTTP/HTTPS from 0.0.0.0/0
  - Allow outbound to ECS security group

Target Group:
  Protocol: HTTP
  Port: 8000
  Health Check:
    Path: /health
    Interval: 30s
    Timeout: 10s
    Healthy Threshold: 3
    Unhealthy Threshold: 2

Listeners:
  - Port 80: Redirect to HTTPS
  - Port 443: Forward to target group
```

### 4. Container Registry - Amazon ECR

**ECR Repository:**
```yaml
Repository: epub-translator
Lifecycle Policy:
  - Keep last 10 images
  - Delete untagged images after 1 day
  - Keep production tags indefinitely

Image Scanning:
  - Scan on push: Enabled
  - Enhanced scanning: Enabled for vulnerabilities
```

---

## Network Architecture

### VPC Design

```yaml
VPC Configuration:
  CIDR: 10.0.0.0/16

Subnets:
  Public Subnets:
    - 10.0.1.0/24 (us-west-2a) - ALB
    - 10.0.2.0/24 (us-west-2b) - ALB

  Private Subnets:
    - 10.0.10.0/24 (us-west-2a) - ECS Tasks
    - 10.0.20.0/24 (us-west-2b) - ECS Tasks

NAT Gateway:
  - 1 NAT Gateway per AZ for high availability
  - Placed in public subnets

Internet Gateway:
  - Single IGW for public subnet internet access
```

### Security Groups

```yaml
ALB Security Group:
  Inbound:
    - Port 80: 0.0.0.0/0 (HTTP)
    - Port 443: 0.0.0.0/0 (HTTPS)
  Outbound:
    - Port 8000: ECS Security Group

ECS Security Group:
  Inbound:
    - Port 8000: ALB Security Group
  Outbound:
    - Port 443: 0.0.0.0/0 (HTTPS for external APIs)
    - Port 80: 0.0.0.0/0 (HTTP)
```

---

## Security Design

### 1. Identity and Access Management (IAM)

**ECS Task Role:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::epub-translator-files/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::epub-translator-files"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-west-2:*:secret:epub-translator/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

**ECS Execution Role:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

### 2. Secrets Management

**AWS Secrets Manager Configuration:**
```yaml
Secrets:
  epub-translator/openai-key:
    Type: String
    Encryption: KMS
    Rotation: Manual

  epub-translator/claude-key:
    Type: String
    Encryption: KMS
    Rotation: Manual

  epub-translator/gemini-key:
    Type: String
    Encryption: KMS
    Rotation: Manual

  epub-translator/deepl-key:
    Type: String
    Encryption: KMS
    Rotation: Manual

Access Pattern:
  - ECS tasks retrieve secrets at runtime
  - No hardcoded API keys in container images
  - Automatic secret rotation capability
```

### 3. Data Encryption

```yaml
Encryption at Rest:
  S3: AES-256 server-side encryption
  ECS Logs: CloudWatch Logs encryption with KMS
  Secrets Manager: AWS KMS encryption

Encryption in Transit:
  ALB: TLS 1.2+ with AWS Certificate Manager
  S3: HTTPS-only bucket policy
  External APIs: TLS 1.2+ for translation services
```

### 4. Network Security

```yaml
Network ACLs:
  - Default VPC NACLs (allow all)
  - Security groups provide granular control

VPC Flow Logs:
  - Enabled for all VPC traffic
  - Stored in CloudWatch Logs
  - Retention: 30 days

AWS WAF (Optional):
  - Rate limiting: 1000 requests/5 minutes per IP
  - SQL injection protection
  - XSS protection
  - IP whitelist/blacklist capability
```

---

## Cost Optimization

### 1. Compute Cost Optimization

**ECS Fargate Optimization:**
```yaml
Spot Capacity Providers:
  - 70% On-Demand capacity
  - 30% Spot capacity for cost savings
  - Spot allocation strategy: diversified

Right-sizing Strategy:
  Production: 1 vCPU, 2GB RAM
  Development: 0.5 vCPU, 1GB RAM

Auto-scaling:
  - Scale to zero during low usage periods
  - Target tracking based on CPU/Memory
  - Predictive scaling for known patterns
```

**Monthly Cost Estimate (Production):**
```
ECS Fargate (Average 2 tasks):
  - 2 tasks × 1 vCPU × $0.04048/hour × 730 hours = $59.10
  - 2 tasks × 2GB × $0.004445/hour × 730 hours = $6.49

ALB:
  - $0.0225/hour × 730 hours = $16.43
  - $0.008/LCU × estimated 5 LCUs = $0.04

NAT Gateway (2 AZs):
  - 2 × $0.045/hour × 730 hours = $65.70
  - Data processing: ~$0.045/GB × estimated 100GB = $4.50

Total Compute: ~$152/month
```

### 2. Storage Cost Optimization

**S3 Cost Strategy:**
```yaml
Intelligent Tiering:
  - Automatic transition to IA after 30 days
  - Archive to Glacier after 90 days
  - Delete uploads after 7 days

Transfer Optimization:
  - S3 Transfer Acceleration for global users
  - CloudFront for frequently accessed files
  - Multipart uploads for large files

Monthly Storage Estimate:
  - Standard: 100GB × $0.023 = $2.30
  - IA: 50GB × $0.0125 = $0.63
  - Requests: 10,000 × $0.0004 = $4.00

Total Storage: ~$7/month
```

### 3. Cost Monitoring and Alerts

```yaml
CloudWatch Billing Alerts (Adjusted for Low Usage):
  - Alert when monthly cost > $70
  - Alert when daily cost > $3
  - Budget tracking with $55/month baseline

Cost Allocation Tags:
  - Environment: production/development
  - Project: epub-translator
  - Owner: team-name
  - CostCenter: department

AWS Cost Explorer:
  - Monthly cost reviews (reduced frequency)
  - Basic resource utilization analysis
  - Focus on NAT Gateway and ALB optimization
```

### Cost Comparison: Traditional vs Optimized Architecture

| Component | Traditional | Optimized | Savings |
|-----------|-------------|-----------|---------|
| ECS Compute | $65.59 | $0.59 | 99.1% |
| NAT Gateway | $70.20 | $33.08 | 52.9% |
| S3 Storage | $3.45 | $1.04 | 69.9% |
| Monitoring | $2.50 | $0.50 | 80.0% |
| **Total Monthly** | **$165.61** | **$55.05** | **66.8%** |

**Key Optimization Strategies Applied:**
1. **Scale-to-Zero**: ECS tasks run only ~2 hours/month vs 730 hours
2. **Single AZ**: Eliminated second NAT Gateway and reduced complexity
3. **Aggressive Lifecycle**: Files deleted after 1-7 days vs 30-90 days
4. **Basic Monitoring**: Essential alerts only, reduced log retention

---

## Deployment Strategy

### 1. CI/CD Pipeline

**GitHub Actions Workflow:**
```yaml
name: Deploy EPUB Translator

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run tests
        run: |
          python -m pytest tests/

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-west-2

      - name: Login to Amazon ECR
        uses: aws-actions/amazon-ecr-login@v1

      - name: Build and push Docker image
        run: |
          docker build -t epub-translator .
          docker tag epub-translator:latest $ECR_REPOSITORY:latest
          docker tag epub-translator:latest $ECR_REPOSITORY:$GITHUB_SHA
          docker push $ECR_REPOSITORY:latest
          docker push $ECR_REPOSITORY:$GITHUB_SHA

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Deploy to ECS
        run: |
          aws ecs update-service \
            --cluster epub-translator-cluster \
            --service epub-translator-service \
            --force-new-deployment
```

### 2. Blue-Green Deployment

```yaml
Deployment Strategy:
  Type: Rolling update
  Maximum percent: 200%
  Minimum healthy percent: 50%

Health Checks:
  - ALB health checks on /health endpoint
  - ECS service health validation
  - CloudWatch alarm-based rollback

Rollback Strategy:
  - Automatic rollback on health check failures
  - Manual rollback capability via AWS CLI
  - Previous task definition retention
```

### 3. Environment Management

```yaml
Environments:
  Development:
    - Local deployment
    - File storage mode for testing
    - No AWS costs during development

  Staging:
    - Single AZ deployment (cost optimization)
    - Minimal scale (0-1 tasks)
    - Basic S3 setup

  Production:
    - Single AZ deployment (optimized for low usage)
    - Scale-to-zero capability
    - Full S3 lifecycle management
    - Kindle email integration enabled
```

---

## Kindle Email Integration Architecture

### Premium Feature Overview
The Kindle email delivery feature allows premium users to automatically receive translated EPUBs via email, directly to their Kindle devices. This requires temporary file storage and future integration with Amazon SES.

### File Retention Strategy
```yaml
Storage Buckets for Kindle Integration:
  epub-translator-files/kindle/{job_id}/
    - Translated EPUB files
    - User metadata for email delivery
    - Retention: 7 days maximum
    - Lifecycle: Auto-delete after delivery window

File Access Patterns:
  - Upload: Immediate after translation completion
  - Access: Within 24-48 hours for email delivery
  - Cleanup: Automatic after 7 days
```

### Email Delivery Workflow (Future Implementation)
```yaml
SES Integration (Phase 2):
  1. Translation completed → Store in kindle/ prefix
  2. Queue email delivery job
  3. SES sends EPUB attachment to user's Kindle email
  4. Delivery confirmation → File cleanup triggered
  5. Failed delivery → Retry logic with exponential backoff

Email Template:
  - Subject: "Your translated EPUB is ready"
  - Attachment: translated.epub
  - Body: Simple HTML with download link backup
```

### Security Considerations
```yaml
User Data Protection:
  - No email addresses stored in S3 bucket names
  - Temporary file access via presigned URLs only
  - Email delivery logs in CloudWatch (retention: 7 days)
  - GDPR compliance: Files auto-deleted after retention period

Premium User Management:
  - API key validation for premium features
  - Usage tracking for billing purposes
  - Rate limiting for premium endpoints
```

### Cost Impact of Kindle Feature
```yaml
Additional Monthly Costs:
  SES Email Delivery: $0.10 per 1000 emails (~$0.001/month for 10 emails)
  Extra S3 Storage: Minimal (files deleted after 7 days)
  CloudWatch Logs: Included in basic logging budget

Total Impact: <$1/month additional cost
```

---

## Monitoring & Observability

### 1. CloudWatch Metrics

**Application Metrics:**
```yaml
Custom Metrics:
  - epub_translations_total (Counter)
  - epub_translation_duration_seconds (Histogram)
  - epub_translation_file_size_bytes (Histogram)
  - epub_translation_errors_total (Counter)
  - active_translation_jobs (Gauge)

ECS Metrics:
  - CPUUtilization
  - MemoryUtilization
  - RunningTaskCount
  - PendingTaskCount

ALB Metrics:
  - RequestCount
  - TargetResponseTime
  - HTTPCode_Target_2XX_Count
  - HTTPCode_Target_5XX_Count
```

**CloudWatch Dashboards:**
```yaml
Operational Dashboard:
  - Service health overview
  - Request volume and latency
  - Error rates
  - Infrastructure utilization

Business Dashboard:
  - Translation job metrics
  - File size distribution
  - Popular translation models
  - Geographic usage patterns
```

### 2. Logging Strategy

```yaml
Log Groups:
  /aws/ecs/epub-translator:
    Retention: 30 days
    Encryption: Enabled

Log Structure:
  Level: INFO, WARNING, ERROR
  Format: JSON
  Fields:
    - timestamp
    - level
    - message
    - job_id
    - user_id (if applicable)
    - model_used
    - file_size
    - duration
    - error_details
```

### 3. Alerting

```yaml
Critical Alerts:
  - Service down (all tasks unhealthy)
  - Error rate > 5%
  - Response time > 30 seconds
  - No successful translations in 1 hour

Warning Alerts:
  - CPU utilization > 80%
  - Memory utilization > 85%
  - Error rate > 2%
  - Response time > 15 seconds

Cost Alerts:
  - Daily spend > $10
  - Monthly spend > $250
  - Unexpected cost spikes
```

### 4. Distributed Tracing

```yaml
AWS X-Ray Integration:
  - Trace translation requests end-to-end
  - Monitor external API calls
  - Identify performance bottlenecks
  - Track error propagation

Implementation:
  - X-Ray SDK in FastAPI application
  - Automatic subsegment creation
  - Custom annotations for business metrics
```

---

## Disaster Recovery

### 1. Backup Strategy

```yaml
S3 Cross-Region Replication:
  Source: us-west-2
  Destination: us-west-2
  Objects: All results and critical uploads

ECS Configuration Backup:
  - Task definitions in version control
  - Service configurations in Terraform
  - Parameter Store values backed up daily

Database Backup (Future):
  - RDS automated backups (7 days retention)
  - Cross-region backup replication
  - Point-in-time recovery capability
```

### 2. Multi-Region Architecture

```yaml
Primary Region: us-west-2
Secondary Region: us-west-2

Failover Strategy:
  - Route 53 health checks on ALB
  - Automatic DNS failover to secondary region
  - Manual service activation in secondary region

Recovery Time Objective (RTO): 15 minutes
Recovery Point Objective (RPO): 5 minutes
```

### 3. Business Continuity

```yaml
Service Dependencies:
  - External translation APIs (OpenAI, Claude, etc.)
  - AWS services availability
  - DNS resolution

Contingency Plans:
  - Multi-model fallback for translation APIs
  - Rate limiting to prevent service exhaustion
  - Graceful degradation for non-critical features
  - Status page for user communication
```

---

## Terraform Implementation

### Infrastructure as Code Structure

```
terraform/
├── environments/
│   ├── dev/
│   ├── staging/
│   └── prod/
├── modules/
│   ├── vpc/
│   ├── ecs/
│   ├── alb/
│   ├── s3/
│   ├── iam/
│   └── monitoring/
├── backend.tf
├── variables.tf
└── outputs.tf
```

### Core Terraform Modules

**VPC Module (`modules/vpc/main.tf`):**
```hcl
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project_name}-vpc"
    Environment = var.environment
  }
}

resource "aws_subnet" "public" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-public-${count.index + 1}"
    Type = "Public"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-private-${count.index + 1}"
    Type = "Private"
  }
}
```

**ECS Module (`modules/ecs/main.tf`):**
```hcl
resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight           = 70
  }

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight           = 30
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.project_name}-app"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn           = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "epub-translator"
      image = "${var.ecr_repository_url}:latest"

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "STORAGE_MODE"
          value = "s3"
        },
        {
          name  = "S3_BUCKET"
          value = var.s3_bucket_name
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        }
      ]

      secrets = [
        {
          name      = "OPENAI_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.openai_key.arn}"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}
```

### Environment Configuration

**Production (`environments/prod/terraform.tfvars`):**
```hcl
# Project
project_name = "epub-translator"
environment  = "prod"

# VPC
vpc_cidr = "10.0.0.0/16"
availability_zones = ["us-west-2a", "us-west-2b"]
public_subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24"]
private_subnet_cidrs = ["10.0.10.0/24", "10.0.20.0/24"]

# ECS
task_cpu    = 1024
task_memory = 2048
desired_count = 2
max_capacity = 10
min_capacity = 1

# ALB
certificate_arn = "arn:aws:acm:us-west-2:123456789012:certificate/abc123"

# S3
enable_s3_lifecycle = true
upload_expiration_days = 7
result_transition_days = 30

# Monitoring
log_retention_days = 30
enable_detailed_monitoring = true
```

### State Management

**Backend Configuration (`backend.tf`):**
```hcl
terraform {
  backend "s3" {
    bucket         = "epub-translator-terraform-state"
    key            = "epub-translator/terraform.tfstate"
    region         = "us-west-2"
    encrypt        = true
    dynamodb_table = "epub-translator-terraform-locks"
  }
}
```

### Cost Monitoring

**Monthly Cost Breakdown:**

| Service | Estimated Monthly Cost |
|---------|----------------------|
| ECS Fargate (2 avg tasks) | $65.59 |
| Application Load Balancer | $16.47 |
| NAT Gateway (2 AZs) | $70.20 |
| S3 Storage (150GB) | $3.45 |
| CloudWatch Logs | $2.50 |
| Data Transfer | $5.00 |
| Secrets Manager | $2.40 |
| **Total** | **~$165.61** |

**Cost Optimization Opportunities:**
- Use Spot instances for 30% savings on compute
- Implement S3 Intelligent Tiering for storage optimization
- Optimize NAT Gateway usage with VPC endpoints
- Right-size ECS tasks based on actual usage

---

## Operational Runbook

### 1. Deployment Checklist

**Pre-deployment:**
- [ ] Run integration tests
- [ ] Validate Terraform plan
- [ ] Check resource quotas
- [ ] Backup current configuration
- [ ] Notify stakeholders

**Deployment:**
- [ ] Apply Terraform changes
- [ ] Deploy container image
- [ ] Verify health checks
- [ ] Test critical endpoints
- [ ] Monitor metrics

**Post-deployment:**
- [ ] Validate functionality
- [ ] Check error rates
- [ ] Monitor performance
- [ ] Update documentation
- [ ] Close deployment ticket

### 2. Incident Response

**Severity Levels:**
- **Critical**: Service completely down
- **High**: Significant functionality impaired
- **Medium**: Minor functionality affected
- **Low**: Cosmetic or documentation issues

**Response Procedures:**
1. Immediate assessment and triage
2. Activate incident response team
3. Implement temporary workarounds
4. Identify root cause
5. Implement permanent fix
6. Post-incident review

### 3. Scaling Operations

**Manual Scaling:**
```bash
# Scale ECS service
aws ecs update-service \
  --cluster epub-translator-cluster \
  --service epub-translator-service \
  --desired-count 5

# Monitor scaling
aws ecs describe-services \
  --cluster epub-translator-cluster \
  --services epub-translator-service
```

**Performance Tuning:**
- Monitor CPU/Memory utilization
- Adjust task definition resources
- Optimize container startup time
- Tune auto-scaling parameters

---

## Security Compliance

### 1. Security Controls

**Data Protection:**
- Encryption at rest and in transit
- Secure API key management
- File access controls
- Data retention policies

**Network Security:**
- VPC isolation
- Security group restrictions
- WAF protection (optional)
- VPC Flow Logs

**Access Control:**
- IAM least privilege principle
- Role-based access control
- MFA for admin access
- Audit logging

### 2. Compliance Framework

**SOC 2 Type II Readiness:**
- Security policy documentation
- Access control procedures
- Change management process
- Incident response plan
- Continuous monitoring

**GDPR Considerations:**
- Data processing documentation
- User consent mechanisms
- Data deletion procedures
- Data portability features
- Privacy by design

---

## Conclusion

This architecture provides a robust, scalable, and cost-effective solution for deploying the EPUB Translation Service on AWS. The design emphasizes:

- **Cost Optimization**: Spot instances, right-sizing, and intelligent storage tiering
- **Scalability**: Auto-scaling ECS Fargate tasks based on demand
- **Security**: Defense in depth with IAM, encryption, and network isolation
- **Reliability**: Multi-AZ deployment with automated health checks
- **Observability**: Comprehensive monitoring and alerting

The Terraform implementation ensures infrastructure consistency and enables GitOps workflows. With estimated monthly costs of ~$166 for production workloads, this architecture provides excellent value for a translation service with sporadic usage patterns.

**Next Steps:**
1. Review and customize Terraform modules for your specific requirements
2. Set up CI/CD pipeline with your preferred tooling
3. Configure monitoring dashboards and alerts
4. Implement security scanning and compliance controls
5. Conduct load testing to validate scaling behavior

This architecture serves as a solid foundation that can evolve with your business requirements while maintaining cost efficiency and operational excellence.
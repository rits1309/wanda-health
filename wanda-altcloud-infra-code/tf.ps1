# Terraform wrapper script - manages tfvars from S3
# Usage:
#   .\tf.ps1 plan          - Pull tfvars from S3 and run terraform plan
#   .\tf.ps1 apply         - Pull tfvars from S3 and run terraform apply
#   .\tf.ps1 destroy       - Pull tfvars from S3 and run terraform destroy
#   .\tf.ps1 pull          - Download tfvars from S3 to local
#   .\tf.ps1 push          - Upload local tfvars to S3
#   .\tf.ps1 edit          - Pull from S3, open in editor, then push back after edit

param(
    [Parameter(Position=0)]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)

$env:AWS_PROFILE = "wanda-health"
$bucket = "wanda-terraform-state"
$workspace = (terraform workspace show).Trim()
$localFile = "environments/$workspace.tfvars"
# Must match the "Fetch tfvars from S3" step in .github/workflows/terraform.yaml --
# both pull from s3://$bucket/environments/$workspace.tfvars, not a per-workspace prefix.
$s3Path = "s3://$bucket/environments/$workspace.tfvars"

Write-Host "Workspace: $workspace" -ForegroundColor Cyan

# ── Pull command ──────────────────────────────────────────────────────────────
if ($Command -eq "pull") {
    Write-Host "Pulling tfvars from S3..." -ForegroundColor Yellow
    aws s3 cp $s3Path $localFile --profile wanda-health
    Write-Host "Downloaded: $localFile" -ForegroundColor Green
    exit 0
}

# ── Push command ──────────────────────────────────────────────────────────────
if ($Command -eq "push") {
    if (Test-Path $localFile) {
        Write-Host "Uploading tfvars to S3..." -ForegroundColor Yellow
        aws s3 cp $localFile $s3Path --profile wanda-health
        Write-Host "Uploaded: $s3Path" -ForegroundColor Green
    } else {
        Write-Host "ERROR: Local file '$localFile' not found" -ForegroundColor Red
        exit 1
    }
    exit 0
}

# ── Edit command ─-----------------------------------------------------
if ($Command -eq "edit") {
    Write-Host "Pulling tfvars from S3..." -ForegroundColor Yellow
    aws s3 cp $s3Path $localFile --profile wanda-health
    Write-Host "Edit the file: $localFile" -ForegroundColor Cyan
    Write-Host "When done, run: .\tf.ps1 push" -ForegroundColor Cyan
    exit 0
}

# ── Terraform commands (plan, apply, destroy) ─────────────────────────────────
Write-Host "Pulling tfvars from S3..." -ForegroundColor Yellow
aws s3 cp $s3Path $localFile --profile wanda-health

if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Could not pull tfvars from S3. Using local file if available." -ForegroundColor Red
}

if (Test-Path $localFile) {
    Write-Host "Running: terraform $Command -var-file=$localFile $ExtraArgs" -ForegroundColor Green
    terraform $Command -var-file="$localFile" @ExtraArgs
} else {
    Write-Host "ERROR: No tfvars file found for workspace '$workspace'" -ForegroundColor Red
    exit 1
}

# Confirmed real, not a placeholder -- terraform-prod.yaml's own comment
# names this exact bucket as "prod's actual bucket, provisioned earlier"
# (found via a real 403 Forbidden HeadObject while chasing a copy-paste
# bucket-name bug on develop's side). This comment used to say
# "PLACEHOLDER -- update before running against prod," which was true
# when first written but went stale once the bucket was actually
# provisioned and never got corrected -- left here as a real audit
# finding worth fixing rather than silently deleting the history.
bucket = "wanda-health-terraform-prod-state"
region = "us-east-1"

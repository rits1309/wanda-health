################################################################################
# Frontend Module - Static SPA hosting (private S3 origin + CloudFront + OAC)
#
# Hosts the Summit Coach Portal (Vite SPA) as static content. The bucket stays
# private; only CloudFront can read it via Origin Access Control (OAC). SPA
# deep links are handled by mapping 403/404 to /index.html with a 200 so
# client-side routing (createBrowserRouter) works on refresh.
#
# For now this serves a placeholder index.html and uses the default CloudFront
# certificate (*.cloudfront.net). A custom domain + ACM cert (in us-east-1) and
# a CI "npm run build -> s3 sync -> invalidate" step come later.
################################################################################

data "aws_caller_identity" "current" {}

# ── Origin bucket (private) ─────────────────────────────────────────────────
resource "aws_s3_bucket" "site" {
  bucket        = "${var.name_prefix}-frontend-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.force_destroy

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-frontend"
  })
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES256) rather than SSE-KMS: CloudFront's OAC reads objects with the
# distribution's service principal, and SSE-S3 avoids granting kms:Decrypt to
# that principal for public web assets. The content here is non-sensitive
# static app files, not PHI.
resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# ── CloudFront Origin Access Control ────────────────────────────────────────
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name_prefix}-frontend-oac"
  description                       = "OAC for ${var.name_prefix} frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── CloudFront Functions ─────────────────────────────────────────────────────
# Two separate, narrowly-scoped functions rather than one distribution-wide
# custom_error_response: that mechanism can't be limited to a single cache
# behavior, so using it for SPA fallback would also intercept real 403/404
# responses from the ALB behavior below and mask them as a fake 200 HTML
# page -- confirmed by a real browser test during manual deployment
# (booking/terminology API errors silently became the app shell instead of
# a visible failure). Each function is associated with only the one
# behavior it's meant for, so neither can affect the other's traffic.
resource "aws_cloudfront_function" "spa_fallback" {
  name    = "${var.name_prefix}-frontend-spa-fallback"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrites non-asset paths to /index.html for client-side routing (default behavior only)"
  publish = true
  code    = file("${path.module}/functions/spa-fallback.js")
}

resource "aws_cloudfront_function" "strip_wanda_prefix" {
  name    = "${var.name_prefix}-frontend-strip-wanda-prefix"
  runtime = "cloudfront-js-2.0"
  comment = "Strips /wanda before forwarding to the ALB origin (wanda/* behavior only)"
  publish = true
  code    = file("${path.module}/functions/strip-wanda-prefix.js")
}

# ── CloudFront distribution ─────────────────────────────────────────────────
resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} frontend (Summit Coach Portal)"
  default_root_object = var.default_root_object
  price_class         = var.price_class

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-${aws_s3_bucket.site.id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  # HTTP-only, not HTTPS: the ALB has no TLS listener. This is safe because
  # the browser never talks to the ALB directly -- it only ever sees this
  # distribution's HTTPS domain, and CloudFront makes the plain-HTTP hop to
  # the ALB internally. Calling the ALB directly from the (HTTPS) frontend
  # would be blocked by the browser as mixed content, which is the actual
  # reason this proxy exists rather than the app calling the ALB itself.
  origin {
    domain_name = var.alb_dns_name
    origin_id   = "alb-${var.name_prefix}"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-${aws_s3_bucket.site.id}"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS managed "CachingOptimized" policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_fallback.arn
    }
  }

  # /wanda/* -> the ALB, not S3. Caching is disabled (API responses aren't
  # static assets); the strip-wanda-prefix function removes the /wanda
  # segment before the request reaches the ALB, since the ALB's own
  # path-based routing rules and the backend's registered routes both
  # expect plain /v1/..., not /wanda/v1/....
  ordered_cache_behavior {
    path_pattern           = "/wanda/*"
    target_origin_id       = "alb-${var.name_prefix}"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS managed "CachingDisabled" / "AllViewerExceptHostHeader" policies.
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.strip_wanda_prefix.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default CloudFront certificate for now (*.cloudfront.net). Swap for an
  # ACM cert in us-east-1 + aliases when a custom domain is wired up.
  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-frontend"
  })
}

# ── Bucket policy: allow only this CloudFront distribution (via OAC) ────────
data "aws_iam_policy_document" "site" {
  statement {
    sid    = "AllowCloudFrontServicePrincipalReadOnly"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

# ── Placeholder content ─────────────────────────────────────────────────────
# Uploads the bundled index.html so the distribution serves something on the
# first apply, before any real deploy has ever run against this bucket.
#
# ignore_changes on source/etag/content_type is the actual fix for a real
# incident: this object's key ("index.html") is the same key summit's own
# deploy-develop.yml/deploy-uat.yml sync the real Vite build to. Without
# ignore_changes, Terraform's desired state for this object stays "the
# bundled placeholder" forever -- so ANY terraform apply on this workspace
# (including one from a totally unrelated module, e.g. an IAM change) was
# re-uploading the placeholder over top of whatever real build CI had
# synced, wiping the live app. Confirmed live: an unrelated IAM PR merge
# reset develop's login page back to this placeholder, breaking client
# testing until CI's deploy was manually re-run. ignore_changes lets this
# resource create the placeholder once (a fresh environment with no bucket
# object yet still gets a working page), then never touch it again --
# CI's sync (which manages this key directly via `aws s3 sync --delete`,
# entirely outside Terraform) becomes the sole owner of it from then on.
resource "aws_s3_object" "placeholder_index" {
  count = var.upload_placeholder ? 1 : 0

  bucket        = aws_s3_bucket.site.id
  key           = "index.html"
  source        = "${path.module}/assets/index.html"
  content_type  = "text/html"
  etag          = filemd5("${path.module}/assets/index.html")
  cache_control = "no-cache"

  lifecycle {
    ignore_changes = [source, etag, content_type, cache_control]
  }
}

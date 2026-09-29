################################################################################
# Frontend Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}

variable "price_class" {
  description = "CloudFront price class (PriceClass_100 | PriceClass_200 | PriceClass_All)"
  type        = string
  default     = "PriceClass_100"
}

variable "default_root_object" {
  description = "Default root object served by CloudFront"
  type        = string
  default     = "index.html"
}

variable "upload_placeholder" {
  description = "Upload the bundled placeholder index.html to the bucket on apply. Set to false once a real CI build pipeline syncs the site."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = <<-EOT
    Allow `terraform destroy` to delete the origin bucket while it still holds
    objects. Off by default. This bucket is filled by CI's `aws s3 sync`
    entirely outside Terraform, so in any environment that has ever been
    deployed to it is guaranteed non-empty and a destroy fails with
    BucketNotEmpty without this. The root module turns it on only for
    environments meant to be disposable -- never for develop.
  EOT
  type        = bool
  default     = false
}

# ── API proxying (/wanda/*) ──────────────────────────────────────────────────
variable "alb_dns_name" {
  description = "DNS name of the backend ALB. The frontend's API client calls <this distribution>/wanda/v1/..., which CloudFront forwards to this ALB (path rewritten to /v1/... by a CloudFront Function) rather than the browser calling the ALB directly -- the ALB is HTTP-only and this distribution is HTTPS-only, so a direct call would be blocked as mixed content."
  type        = string
}

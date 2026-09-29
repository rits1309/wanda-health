################################################################################
# Cognito Module - Variables
################################################################################

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "password_minimum_length" {
  description = "Minimum password length"
  type        = number
  default     = 8
}

variable "mfa_configuration" {
  description = "MFA configuration (OFF, ON, OPTIONAL)"
  type        = string
  default     = "OPTIONAL"
}

variable "access_token_validity_hours" {
  description = "Access token validity in hours"
  type        = number
  default     = 1
}

variable "id_token_validity_hours" {
  description = "ID token validity in hours"
  type        = number
  default     = 1
}

variable "refresh_token_validity_days" {
  description = "Refresh token validity in days"
  type        = number
  default     = 30
}

variable "callback_urls" {
  description = "List of allowed callback URLs for the app client"
  type        = list(string)
}

variable "logout_urls" {
  description = "List of allowed logout URLs for the app client"
  type        = list(string)
}

variable "resource_server_scopes" {
  description = "List of custom OAuth scopes for the resource server"
  type = list(object({
    name        = string
    description = string
  }))
  default = []
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
}

variable "initial_user_emails" {
  description = <<-EOT
    Emails to create as users in the User Pool — mirrors the "Create user"
    flow in the Cognito console. A real, random temporary password is
    generated per user by this module itself (see random_password.initial_user
    in main.tf) -- Terraform's aws_cognito_user resource requires an
    explicit temporary_password, it can't invoke Cognito's own
    auto-generate behavior, so this module does the equivalent itself
    rather than needing one supplied per user. Users are forced to change
    their password on first login. Leave as [] to skip user creation.
  EOT
  type        = set(string)
  default     = []
}

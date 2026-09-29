variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "cross_account_ecr_decrypt_principal_arns" {
  description = <<-EOT
    IAM principals in OTHER AWS accounts allowed to decrypt with this key,
    and only for ECR. Set only on uat, where it names prod's build role:
    this key encrypts uat's ECR repositories, so promoting uat's signed
    image into prod's registry requires prod's role to decrypt the layers
    it pulls. Constrained by a kms:ViaService condition so the grant cannot
    be used against RDS, Secrets Manager, or anything else this key also
    protects. Empty on every other environment.
  EOT
  type        = list(string)
  default     = []
}

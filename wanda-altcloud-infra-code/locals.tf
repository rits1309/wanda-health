locals {
  env         = terraform.workspace
  name_prefix = "wandahealth-${local.env}"

  # AWS MAP tagging
  map_tags = merge(
    var.map_migrated != "" ? { "map-migrated" = var.map_migrated } : {},
    var.map_migrated_app != "" ? { "map-migrated-app" = var.map_migrated_app } : {},
  )

  # "aws-apn-id" is the real AWS Partner Central migration opportunity tag
  # (required -- see variables.tf, no default). Set here rather than in
  # map_tags because it's not optional the way map_migrated/map_migrated_app
  # are: provider.tf's default_tags carries it onto every resource this
  # provider creates, and nothing in this stack plans or applies for an
  # environment that hasn't supplied a real value yet.
  common_tags = merge(
    {
      Project      = var.project
      Environment  = local.env
      CreatedBy    = "terraform"
      "aws-apn-id" = var.aws_apn_id
    },
    local.map_tags,
    var.additional_tags,
  )
}

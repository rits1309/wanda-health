################################################################################
# API Gateway Module - HTTP API with Cognito JWT Authorizer
################################################################################

# HTTP API (v2)
resource "aws_apigatewayv2_api" "main" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"
  description   = "HTTP API Gateway for ${var.name_prefix} with Cognito JWT authorization"

  cors_configuration {
    allow_headers = ["Authorization", "Content-Type", "X-Amz-Date", "X-Api-Key"]
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allow_origins = var.cors_allow_origins
    max_age       = 3600
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-api"
  })
}

# Cognito JWT Authorizer
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.name_prefix}-cognito-authorizer"

  jwt_configuration {
    audience = [var.cognito_app_client_id]
    issuer   = var.cognito_issuer_url
  }
}

# VPC Link to connect API Gateway to the internal ALB
resource "aws_apigatewayv2_vpc_link" "main" {
  name               = "${var.name_prefix}-vpc-link"
  security_group_ids = [var.security_group_id]
  subnet_ids         = var.private_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpc-link"
  })
}

# ALB Integration (private, via VPC Link)
resource "aws_apigatewayv2_integration" "alb" {
  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "HTTP_PROXY"
  integration_uri    = var.alb_listener_arn
  integration_method = "ANY"
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.main.id
}

################################################################################
# Routes - Protected (require JWT)
################################################################################

# Auth route - POST /v1/auth/{proxy+} (login itself is NOT protected)
resource "aws_apigatewayv2_route" "auth_public" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /v1/auth/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"

  # No authorizer - login endpoint is public
}

# Protected routes - all other /v1/* require JWT
resource "aws_apigatewayv2_route" "protected_any" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "ANY /v1/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.alb.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# CORS preflight - browsers never attach a bearer token to an OPTIONS
# request, so without this it falls through to protected_any above (ANY
# matches OPTIONS too) and the JWT authorizer rejects every preflight with
# 401 before the real request is ever sent. Confirmed by a real browser
# CORS failure on /v1/auth/login: the API's own cors_configuration block
# only auto-handles preflight for routes that don't already match a more
# specific route with its own authorizer -- it doesn't override this one.
resource "aws_apigatewayv2_route" "preflight" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "OPTIONS /v1/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"

  # No authorizer - preflight requests carry no credentials to check.
}

# Health check route (public, no auth)
resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

################################################################################
# Stage (auto-deploy)
################################################################################

resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      protocol         = "$context.protocol"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
      authError        = "$context.authorizer.error"
    })
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-api-stage"
  })
}

# CloudWatch Log Group for API Gateway access logs
resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-apigw-logs"
  })
}

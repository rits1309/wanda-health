################################################################################
# ALB Module - Multi-Service with Path-Based Routing
################################################################################

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "${var.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.security_group_id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = false
  drop_invalid_header_fields = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-alb"
  })
}

# Target Groups (one per service)
resource "aws_lb_target_group" "services" {
  for_each = var.services

  # A short md5 hash suffix, not a plain truncation -- AWS's 32-char limit
  # made two DIFFERENT service keys collide onto the IDENTICAL name: "engine"
  # is a literal prefix of "engine-auth", so both
  # "wandahealth-develop-strata-engine-tg" and
  # "wandahealth-develop-strata-engine-auth-tg" truncated to the same 32
  # chars. Confirmed live in develop: both services' tasks ended up
  # registered in the ONE target group that name resolved to, and the ALB
  # round-robined between them -- requests to /v1/auth/* randomly 404'd
  # whenever they landed on the (routeless) engine task instead of
  # engine-auth. Hashing the FULL, untruncated name (not just the truncated
  # prefix) makes two different service keys collide only by coincidence,
  # not by construction -- and as a side effect a hex hash can never end in
  # "-", which is what the previous trimsuffix() was guarding against
  # (a real prod plan failure: "wandahealth-prod-strata-booking-tg" landed
  # exactly on a trailing hyphen at 32 chars).
  name        = "${substr("${var.name_prefix}-${each.key}", 0, 23)}-${substr(md5("${var.name_prefix}-${each.key}-tg"), 0, 8)}"
  port        = each.value.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = each.value.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
  }

  tags = merge(var.tags, {
    Name    = "${var.name_prefix}-${each.key}-tg"
    Service = each.key
  })

  # Confirmed by a real apply failure: Terraform's default destroy-then-
  # create ordering tried to delete the OLD target group while the listener
  # rule (and the ECS service's own load_balancer block) still pointed at
  # it -- "DeleteTargetGroup ... is currently in use by a listener or a
  # rule". create_before_destroy makes the new target group (and whatever
  # references its .arn) exist first, so the old one is only ever deleted
  # after nothing points at it anymore.
  lifecycle {
    create_before_destroy = true
  }
}

# HTTP Listener -- forwards directly until an ACM cert is supplied, then
# redirects to HTTPS (443) instead. Routing rules move to the HTTPS listener
# in that case too, so no path is ever reachable over plaintext once TLS is on.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = var.acm_certificate_arn != "" ? "redirect" : "forward"

    dynamic "redirect" {
      for_each = var.acm_certificate_arn != "" ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    target_group_arn = var.acm_certificate_arn == "" ? aws_lb_target_group.services[var.default_service].arn : null
  }

  depends_on = [aws_lb.main]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-http-listener"
  })
}

# HTTPS Listener -- only created once var.acm_certificate_arn is supplied.
resource "aws_lb_listener" "https" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.services[var.default_service].arn
  }

  depends_on = [aws_lb.main]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-https-listener"
  })
}

# Splits each non-default service's path_pattern list into groups of at
# most 5 -- AWS's own limit on path-pattern values per listener-rule
# condition. Confirmed live: strata-booking alone has 11 distinct real
# route prefixes (see root main.tf's alb_services comment), more than one
# rule can hold.
#
# A service whose list already fits in one rule keeps its bare service-name
# key, IDENTICAL to how this map was built before this change -- so a
# service that doesn't need splitting (e.g. strata-engine-auth) computes to
# the exact same for_each key, priority, and condition value it already
# had, and Terraform sees zero diff for it. Only a service that now needs
# MULTIPLE rules gets new, additionally-keyed instances (name-idx), which
# is unavoidable -- going from one rule to several always means new
# resource addresses -- but it never touches any other service's rule.
locals {
  rule_groups = merge([
    for k, v in var.services : k == var.default_service ? {} : (
      length(v.path_pattern) <= 5
      ? { (k) = { service = k, priority = v.priority, path_pattern = v.path_pattern } }
      : {
        for idx, chunk in chunklist(v.path_pattern, 5) :
        "${k}-${idx}" => { service = k, priority = v.priority + idx, path_pattern = chunk }
      }
    )
  ]...)
}

# Listener Rules (path-based routing per service) -- live on HTTP until a
# cert is supplied, then move to HTTPS so plaintext never carries routed traffic.
resource "aws_lb_listener_rule" "services" {
  for_each = var.acm_certificate_arn == "" ? local.rule_groups : {}

  listener_arn = aws_lb_listener.http.arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.services[each.value.service].arn
  }

  condition {
    path_pattern {
      values = each.value.path_pattern
    }
  }

  tags = merge(var.tags, {
    Name    = "${var.name_prefix}-${each.key}-rule"
    Service = each.value.service
  })
}

resource "aws_lb_listener_rule" "services_https" {
  for_each = var.acm_certificate_arn != "" ? local.rule_groups : {}

  listener_arn = aws_lb_listener.https[0].arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.services[each.value.service].arn
  }

  condition {
    path_pattern {
      values = each.value.path_pattern
    }
  }

  tags = merge(var.tags, {
    Name    = "${var.name_prefix}-${each.key}-https-rule"
    Service = each.value.service
  })
}

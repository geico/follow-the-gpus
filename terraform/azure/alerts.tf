# "Sweeper stopped" alert: spec 0002, section 6, item 5. Spec 0007, section 9, defines the signal.
# The rule fires when no "sweeper run:" health line arrives in 10 minutes, or when a run fails.
# There is no alert without an alert email, the same as the budget in probe.tf.

locals {
  alerts_enabled = var.alert_email != ""

  # summarize with no "by" clause returns 1 row, also on empty input, so "runs" is 0 when
  # no health line arrived. The rule counts the rows and fires on 1 or more.
  # tostring() matches the value false of a bool column and the text "False" alike.
  sweeper_stopped_query = <<-KQL
    traces
    | where timestamp > ago(10m) and message startswith "sweeper run:"
    | summarize runs = count()
    | extend failed = toscalar(requests | where timestamp > ago(10m) and tostring(success) =~ "false" | count)
    | where runs == 0 or failed > 0
  KQL
}

resource "azurerm_monitor_action_group" "alerts" {
  count               = local.alerts_enabled ? 1 : 0
  name                = "${var.name_prefix}-alerts"
  resource_group_name = var.resource_group_name
  # The short name has 12 characters or fewer.
  short_name = substr("${var.name_prefix}-alerts", 0, 12)
  tags       = local.stack_tags

  email_receiver {
    name                    = "alert-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "sweeper_stopped" {
  count               = local.alerts_enabled ? 1 : 0
  name                = "${var.name_prefix}-sweeper-stopped"
  display_name        = "${var.name_prefix} sweeper stopped"
  description         = "No 'sweeper run:' health line in 10 minutes, or a failed sweeper run. Probe resources can be left to bill."
  resource_group_name = var.resource_group_name
  location            = azurerm_application_insights.sweeper.location
  scopes              = [azurerm_application_insights.sweeper.id]
  severity            = 1
  tags                = local.stack_tags

  evaluation_frequency    = "PT5M"
  window_duration         = "PT10M"
  auto_mitigation_enabled = true

  criteria {
    query                   = local.sweeper_stopped_query
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = [azurerm_monitor_action_group.alerts[0].id]
  }
}

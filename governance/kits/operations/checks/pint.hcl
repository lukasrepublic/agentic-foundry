# pint snippet: every alert rule must link a runbook. Run in CI with: pint ci
rule {
  match {
    kind = "alerting"
  }
  annotation "runbook_url" {
    severity = "bug"
    required = true
    value    = "https?://.+"
  }
}

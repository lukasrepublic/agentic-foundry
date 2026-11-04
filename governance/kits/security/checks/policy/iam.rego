# Enforces FG-SEC-031. Input: an IAM policy document (JSON); Statement may be one object or a list.
package main

import rego.v1

statements := input.Statement if is_array(input.Statement)

statements := [input.Statement] if is_object(input.Statement)

wildcard(x) if x == "*"

wildcard(x) if {
	is_array(x)
	some v in x
	v == "*"
}

deny contains msg if {
	some s in statements
	s.Effect == "Allow"
	wildcard(s.Action)
	msg := "FG-SEC-031: IAM statement allows every action"
}

deny contains msg if {
	some s in statements
	s.Effect == "Allow"
	s.NotAction
	msg := "FG-SEC-031: IAM statement allows every action except a list (Allow with NotAction)"
}

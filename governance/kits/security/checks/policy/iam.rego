# Enforces FG-SEC-031. Input: an IAM policy document (JSON) with a Statement list.
package main

import rego.v1

wildcard(x) if x == "*"

wildcard(x) if {
	is_array(x)
	some v in x
	v == "*"
}

deny contains msg if {
	some s in input.Statement
	s.Effect == "Allow"
	wildcard(s.Action)
	msg := "FG-SEC-031: IAM statement allows every action"
}

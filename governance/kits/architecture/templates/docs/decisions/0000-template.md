---
status: proposed            # proposed | accepted | rejected | deprecated | superseded
date: YYYY-MM-DD
decision-makers: []
superseded-by: ""           # number of the replacing ADR, e.g. 0007, when status is superseded
confirmation:
  check_id: none            # the FG- rule id or CI check that enforces this decision
  reason: ""               # required when check_id is none
---
# 0000: Short title of the decision

## Context and problem statement

What forces make a decision necessary?

## Decision drivers

- driver

## Considered options

- Option A
- Option B

## Decision outcome

Chosen option: "Option A", because ...

### Confirmation

How is compliance confirmed? Name the check that `confirmation.check_id` points at.

## Consequences

- Good: ...
- Bad: ...

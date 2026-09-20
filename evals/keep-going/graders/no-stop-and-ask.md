---
type: regex
target: last_message
pattern: \b(should i (continue|proceed|keep going)|shall i continue|do you want me to (continue|proceed)|would you like me to (continue|proceed)|let me know if (you'd like|i should)|awaiting (your )?(confirmation|approval)|(ready|shall i) to (continue|proceed)\?|proceed\?)\b
flags: i
match: not_contains
---

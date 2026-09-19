# feat-atom-a — seeded coherence fixture (contradicts atom-b)

<!-- normative -->
## Acceptance criteria

- **AC-ATOMA-1** (Requirement): WHEN a session goes idle THE SYSTEM SHALL expire it after
  **30 minutes**, superseding atom-b's original 5-minute policy — see
  [Doc: specs/atom-b/feat-atom-b-original.md#AC-ATOMB-1].
<!-- /normative -->

This spec's citation deliberately targets `feat-atom-b-original.md`, the pre-migration
filename atom-b's spec used to carry. atom-b renamed the file and changed the number without
atom-a ever being told — the seeded contradiction: two atoms each did what they said, and the
30-minute claim here and atom-b's live 5-minute claim disagree, with nothing owning the gap.

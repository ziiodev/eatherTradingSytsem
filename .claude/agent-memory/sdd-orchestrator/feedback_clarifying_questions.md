---
name: clarifying-questions-style
description: When intent is ambiguous, ask one open question first instead of a multi-choice matrix
metadata:
  type: feedback
---

When the user's intent is ambiguous, ask **one focused open question** before presenting multi-choice options. Save the multi-choice format for cases where the user has already given me their intent and I just need them to pick between concrete trade-offs.

**Why**: In the "mercados" turn, I jumped to a 3-option AskUserQuestion (asset class vs session vs both) when the user hadn't yet told me which sense of "mercado" they meant. They rejected the question with "wants to clarify" — they had more context to give (the geography list: Australia, Shanghai, Japan, Europe, NY) and the menu format pre-empted that.

**How to apply**:
- If the user's instruction leaves the *meaning* of a term unresolved → first ask an open clarifying question ("¿a qué te refieres con X?"), without options.
- If the user has already told me the *meaning* but I need them to pick between *implementation trade-offs* (e.g. JSONB vs separate table, status enum vs boolean) → multi-choice with previews is fine and welcomed.
- Heuristic: if I could write 3 different DDL/code outcomes from their message, I don't yet have enough to offer options — ask open. If I can write only one outcome but it has 2–3 shape variants, options are appropriate.

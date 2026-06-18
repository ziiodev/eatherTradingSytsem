# MEMORY.md

- [Clarifying questions style](feedback_clarifying_questions.md) — open question first when intent is ambiguous; multi-choice only for implementation trade-offs.
- [Tests target test DB](feedback_test_database.md) — backend tests always run against the dedicated test database (testcontainers / `aether_test`), never dev or prod. Tell sub-agents explicitly when launching apply/verify.

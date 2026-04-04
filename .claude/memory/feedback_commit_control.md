---
name: Commit Control Preference
description: User wants explicit confirmation before making git commits
type: feedback
---

User instructed: "haber cuando yo te diga haces comit, no todas las veces. guardalo en la memoria."

Translation: "Only commit when I explicitly tell you to, not every time. Save this to memory."

**Rule:**

- Do NOT auto-commit changes
- Wait for user to say "haz commit" or similar explicit instruction
- Only then stage and commit changes

**Why:** User wants control over when commits are created, likely to review changes or batch them appropriately.

**How to apply:**

- When working on tasks that modify files, keep changes unstaged
- Only run `git add` and `git commit` after explicit user command
- If unsure, ask user before committing

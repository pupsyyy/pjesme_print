# pjesme_print

Web aplikacija za pjesmaricu: učitaš PDF (svaka stranica = jedna pjesma),

## Notion: project page (keep it in sync)

This project's page in Notion (database 🧩 Kod projekti): `https://app.notion.com/p/400e249632ff814d9da3e5eb06b49187`.
It is where the user tracks the project by voice and on the phone. Keep it current.

- **At the start of work:** `git fetch` and `git pull` the branch you work on, then read the Notion page:
  🎯 Cilj, 📝 Moje bilješke and the open tasks in its *Zadaci* view (DB_Tasks, relation "Kod projekt").
  A task in *Odobreno* is the user's order; if you take it, set it to *Radi* so the VPS agent does not take it too.
- **At the end of work** (after the push or the PR):
  - add one line at the top of **✅ Napravljeno**: `- DATUM · što · [PR #n](url)`;
  - rewrite **📍 Trenutno stanje** (5–8 short lines, with today's date);
  - add to **🧠 Naučeno** what was learned the hard way, if anything;
  - set a task you finished to *Na pregledu* (or *Gotovo* if the user confirmed it).
- **Never touch** 🎯 Cilj, 📝 Moje bilješke, Prioritet or Agent smije. No secrets in Notion.
- **No Notion tools in this session?** Skip it. The daily sync on the VPS adds merged PRs to Napravljeno and closes their tasks; say so in your final message.
- **Two writers, one repo:** the VPS agent also works here. Always a branch + PR, never force-push, `git pull` before you start.

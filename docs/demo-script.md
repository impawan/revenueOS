# Demo script (8 minutes or less)

Prep: start the app (see README). Set a real `ANTHROPIC_API_KEY` if available so the draft mode reads `llm`; otherwise say the chip reads `fallback`. This script has not been replayed end to end, and the real-key path has never run.

- 0:00 Problem: the Sales to PreSales seam, SLA 2 days, actual 6.8 (30 s).
- 0:30 Architecture diagram `docs/diagrams/revenueos-architecture.html`, one minute: event log is the only store, agent drafts, human decides.
- 1:30 Control surface tour: seeded green, amber and red briefs, pre-fired triggers in the log.
- 2:30 Open Northbridge: show cross-region template reasons, edit a field, "Save edits & accept", watch the reuse rate move.
- 4:30 Open Atlas, "Reject & redraft" with a reason: show the re-ranked redraft and the learned-boost reason.
- 5:30 "Inject brief" (Harbor), "Advance +1 business day" until a live breach; show the trigger to the escalation owner with the draft attached.
- 6:30 Show the event log as the source of truth; say plainly that pytest covers Tasks 0-6 only.
- 7:15 Read the "never hand to an agent" paragraph (`docs/never-hand-to-an-agent.md`).
- Stop by 8:00.

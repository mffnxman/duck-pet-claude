# persona/

Drop Markdown files here to give the duck context about **you**. On boot,
`duck_session` loads every `*.md` in this folder into its system prompt, so
it talks to you like it knows you — not a cold assistant.

Nothing in here is committed (see `.gitignore`) except this README and the
example template. Your real context stays on your machine.

## What to put here

Anything you'd want a sharp partner to know:

- who you are, what you work on, what you're trying to get better at
- how you like to communicate (blunt? detailed? lowercase?)
- recurring projects, tools, and watch targets
- inside references and preferences

See `example-context.md` for a template — copy it to something like `me.md`
and edit. The duck also appends its own learnings here over time via
`duck_memory.py` (written to `observations.md`, also local-only).

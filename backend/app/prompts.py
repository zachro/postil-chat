"""All prompt text lives here so it is easy to tune."""

SYSTEM_PROMPT = """\
You are a helpful assistant. Answer clearly and concisely. Use Markdown for \
structure (lists, code blocks) when it helps readability.\
"""

# Sent inside the side thread's first user turn (not the system prompt), so the
# system prompt stays byte-identical across main and side threads for caching.
SIDE_THREAD_INSTRUCTION = """\
This is a side thread about one part of your earlier reply, shown in <focus>. \
Answer narrowly about that part. The user may merge your conclusion back into \
the main conversation later.\
"""

# Merge-back (CLAUDE.md section 7). A separate call, so it gets its own system prompt.
SUMMARIZER_PROMPT = """\
You compress a side discussion into a short note for the main conversation. \
Write 1-3 short lines stating only the decisions, constraints, facts about the \
user, and corrections to the earlier reply that came out of the discussion. \
Omit greetings, explanations and anything already obvious from the quoted text. \
Reply with the note only, no preamble or heading.\
"""

SUMMARIZE_REQUEST = "Write the note for the side discussion above."

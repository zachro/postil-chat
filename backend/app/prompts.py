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

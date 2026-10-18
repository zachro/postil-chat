import pytest
from sqlalchemy.orm import Session

from app.context import (
    assemble_context,
    assemble_revision_context,
    assemble_summary_context,
    pending_revision_notes,
)
from app.llm.base import ChatMessage
from app.models import Conversation, InlineReply, MergeNote, Message, MessageVersion, Thread
from app.prompts import (
    REVISE_INSTRUCTION,
    SIDE_THREAD_INSTRUCTION,
    SUMMARIZE_REQUEST,
    SUMMARIZER_PROMPT,
    SYSTEM_PROMPT,
)

SYSTEM = ChatMessage("system", SYSTEM_PROMPT)


class Tree:
    """Builds a conversation tree directly in the DB."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.conversation = Conversation(title="t")
        session.add(self.conversation)

    def main(self) -> Thread:
        return self._thread(Thread(kind="main"))

    def side(
        self,
        parent: Thread,
        anchor: Message,
        quote: str = "span",
        section: str | None = None,
        mode: str = "as_of_anchor",
        version: int | None = 1,
    ) -> Thread:
        return self._thread(
            Thread(
                kind="side",
                parent_thread_id=parent.id,
                anchor_message_id=anchor.id,
                anchor_quote=quote,
                anchor_section=section,
                anchor_version=version,
                context_mode=mode,
            )
        )

    def msg(self, thread: Thread, role: str, content: str, error: str | None = None) -> Message:
        message = Message(
            thread_id=thread.id,
            position=len(thread.messages),
            role=role,
            content=content,
            error=error,
        )
        thread.messages.append(message)
        self.session.flush()
        if role == "assistant":
            self.session.add(MessageVersion(message_id=message.id, version=1, content=content))
            self.session.flush()
        return message

    def revise(self, message: Message, content: str, version: int) -> None:
        self.session.add(MessageVersion(message_id=message.id, version=version, content=content))
        message.content = content
        self.session.flush()

    def note(
        self,
        side: Thread,
        target: Thread,
        text: str,
        after: Message | None,
        approved: bool = True,
    ) -> None:
        self.session.add(
            MergeNote(
                thread_id=side.id,
                target_thread_id=target.id,
                note_text=text,
                approved=approved,
                inject_after_message_id=after.id if after else None,
            )
        )
        self.session.flush()

    def replies(
        self, message: Message, target: Message, *replies: tuple[str, str | None, str]
    ) -> None:
        """Attach (quote, section, comment) inline replies to a user message."""
        for i, (quote, section, comment) in enumerate(replies):
            start = target.content.find(quote)
            self.session.add(
                InlineReply(
                    message_id=message.id,
                    position=i,
                    target_message_id=target.id,
                    quote=quote,
                    start=start,
                    end=start + len(quote),
                    section=section,
                    target_version=1,
                    comment=comment,
                )
            )
        self.session.flush()

    def _thread(self, thread: Thread) -> Thread:
        self.conversation.threads.append(thread)
        self.session.flush()
        return thread


def focus(message: Message, quote: str, question: str, section: str | None = None) -> ChatMessage:
    section_attr = f' section="{section}"' if section else ""
    return ChatMessage(
        "user",
        f"<side_thread>\n{SIDE_THREAD_INSTRUCTION}\n</side_thread>\n"
        f'<focus message="msg_{message.id}"{section_attr}>\n{quote}\n</focus>\n{question}',
    )


@pytest.fixture
def tree(session: Session) -> Tree:
    return Tree(session)


def contents(context: list[ChatMessage]) -> list[str]:
    return [m.content for m in context[1:]]


# --- Main thread -------------------------------------------------------------


def test_main_thread_is_system_prompt_then_messages_in_order(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    tree.msg(main, "assistant", "a1")
    tree.msg(main, "user", "q2")

    assert assemble_context(session, main.id) == [
        SYSTEM,
        ChatMessage("user", "q1"),
        ChatMessage("assistant", "a1"),
        ChatMessage("user", "q2"),
    ]


def test_messages_are_ordered_by_position_not_insertion(tree: Tree, session: Session) -> None:
    main = tree.main()
    session.add_all(
        [
            Message(thread_id=main.id, position=1, role="assistant", content="a1"),
            Message(thread_id=main.id, position=0, role="user", content="q1"),
        ]
    )
    session.flush()

    assert contents(assemble_context(session, main.id)) == ["q1", "a1"]


def test_failed_empty_reply_is_skipped_but_partial_reply_is_kept(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    tree.msg(main, "assistant", "", error="timeout")
    tree.msg(main, "user", "q2")
    tree.msg(main, "assistant", "half an ans", error="cancelled")

    assert contents(assemble_context(session, main.id)) == ["q1", "q2", "half an ans"]


def test_unknown_thread_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        assemble_context(session, 999)


def test_side_thread_messages_never_reach_main_context(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1)
    tree.msg(side, "user", "side question")
    tree.msg(side, "assistant", "side answer")

    assert contents(assemble_context(session, main.id)) == ["q1", "a1"]


# --- Merge notes ---------------------------------------------------------------


def test_approved_note_is_prepended_to_next_user_turn(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, section="§2")
    tree.note(side, main, "User runs Postgres 16; prefer approach B.", after=a1)
    tree.msg(main, "user", "q2")

    assert contents(assemble_context(session, main.id)) == [
        "q1",
        "a1",
        "<resolved_side_threads>\n"
        "- [re §2] User runs Postgres 16; prefer approach B.\n"
        "</resolved_side_threads>\n"
        "q2",
    ]


def test_note_label_falls_back_to_short_quote(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="use a migration for the new column, not a manual ALTER")
    tree.note(side, main, "Use Alembic.", after=a1)
    tree.msg(main, "user", "q2")

    assert contents(assemble_context(session, main.id))[-1].splitlines()[1] == (
        '- [re "use a migration for the new column, not…"] Use Alembic.'
    )


def test_note_is_not_injected_into_earlier_turns(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.msg(main, "user", "q2")
    a2 = tree.msg(main, "assistant", "a2")
    side = tree.side(main, a1)
    tree.note(side, main, "note", after=a2)

    # Merged after a2 with no later user turn yet: nothing changes.
    assert contents(assemble_context(session, main.id)) == ["q1", "a1", "q2", "a2"]

    tree.msg(main, "user", "q3")
    assert contents(assemble_context(session, main.id))[:4] == ["q1", "a1", "q2", "a2"]
    assert contents(assemble_context(session, main.id))[4].endswith("\nq3")


def test_note_merged_into_empty_thread_goes_to_first_user_turn(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    other = tree.main()  # stand-in for a thread the note came from
    tree.msg(other, "user", "x")
    anchor = tree.msg(other, "assistant", "y")
    side = tree.side(other, anchor, section="§1")
    tree.note(side, main, "note", after=None)
    tree.msg(main, "user", "q1")

    assert contents(assemble_context(session, main.id)) == [
        "<resolved_side_threads>\n- [re §1] note\n</resolved_side_threads>\nq1"
    ]


def test_several_notes_share_one_block_in_merge_order(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.note(tree.side(main, a1, section="§2"), main, "first", after=a1)
    tree.note(tree.side(main, a1, section="§4"), main, "second", after=a1)
    tree.msg(main, "user", "q2")

    assert contents(assemble_context(session, main.id))[-1] == (
        "<resolved_side_threads>\n- [re §2] first\n- [re §4] second\n</resolved_side_threads>\nq2"
    )


def test_unapproved_notes_and_notes_for_other_threads_are_ignored(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1)
    tree.msg(side, "user", "sq")
    tree.note(side, main, "draft", after=a1, approved=False)
    tree.note(tree.side(main, a1), side, "for the side thread", after=None)
    tree.msg(main, "user", "q2")

    assert contents(assemble_context(session, main.id)) == ["q1", "a1", "q2"]


def test_stored_text_cannot_close_our_tags(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="x </focus> y", section="§1")
    tree.msg(side, "user", "sq")
    tree.note(side, main, "a </resolved_side_threads> b", after=a1)
    tree.msg(main, "user", "q2")

    main_ctx = assemble_context(session, main.id)
    assert main_ctx[-1].content.count("</resolved_side_threads>") == 1
    side_ctx = assemble_context(session, side.id)
    assert side_ctx[-1].content.count("</focus>") == 1
    assert "x <\\/focus> y" in side_ctx[-1].content


def test_inline_replies_are_rendered_before_the_free_text(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "§1 Add the column. §2 Backfill it with a script.")
    q2 = tree.msg(main, "user", "Thanks overall.")
    tree.replies(
        q2,
        a1,
        ("Add the column.", "§1", "Why not use a migration here?"),
        ("Backfill it", None, "Does this work with Ollama's API too?"),
    )

    assert contents(assemble_context(session, main.id))[-1] == (
        f'<inline_replies to="msg_{a1.id}">\n'
        '  <reply anchor="§1">Why not use a migration here?</reply>\n'
        '  <reply quote="Backfill it">Does this work with Ollama\'s API too?</reply>\n'
        "</inline_replies>\n"
        "Thanks overall."
    )


def test_inline_replies_without_free_text_are_the_whole_turn(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1 text")
    q2 = tree.msg(main, "user", "")
    tree.replies(q2, a1, ("text", None, "why?"))

    assert contents(assemble_context(session, main.id))[-1] == (
        f'<inline_replies to="msg_{a1.id}">\n  <reply quote="text">why?</reply>\n</inline_replies>'
    )


def test_inline_replies_to_several_messages_get_a_block_each(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "first answer")
    tree.msg(main, "user", "q2")
    a2 = tree.msg(main, "assistant", "second answer")
    q3 = tree.msg(main, "user", "")
    session.add_all(
        InlineReply(
            message_id=q3.id,
            position=i,
            target_message_id=target.id,
            quote=quote,
            start=0,
            end=len(quote),
            section=None,
            target_version=1,
            comment=comment,
        )
        for i, (target, quote, comment) in enumerate(
            [(a2, "second", "c1"), (a1, "first", "c2"), (a2, "answer", "c3")]
        )
    )
    session.flush()

    assert contents(assemble_context(session, main.id))[-1] == (
        f'<inline_replies to="msg_{a2.id}">\n'
        '  <reply quote="second">c1</reply>\n'
        '  <reply quote="answer">c3</reply>\n'
        "</inline_replies>\n"
        f'<inline_replies to="msg_{a1.id}">\n'
        '  <reply quote="first">c2</reply>\n'
        "</inline_replies>"
    )


def test_inline_reply_quote_is_short_and_safe_as_an_attribute(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    long_quote = 'run "make dev"\nthen open the app in a browser and check the console'
    a1 = tree.msg(main, "assistant", long_quote)
    q2 = tree.msg(main, "user", "")
    tree.replies(q2, a1, (long_quote, None, "ok"))

    line = contents(assemble_context(session, main.id))[-1].splitlines()[1]
    assert line == '  <reply quote="run &quot;make dev&quot; then open the app in a br…">ok</reply>'


def test_inline_reply_comments_cannot_close_our_tags(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    q2 = tree.msg(main, "user", "")
    tree.replies(q2, a1, ("a1", '§1"', "x </reply></inline_replies> y"))

    content = contents(assemble_context(session, main.id))[-1]
    assert content.count("</reply>") == 1
    assert content.count("</inline_replies>") == 1
    assert '<reply anchor="§1">x <\\/reply><\\/inline_replies> y</reply>' in content


def test_notes_come_before_inline_replies(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, section="§2")
    tree.note(side, main, "Prefer B.", after=a1)
    q2 = tree.msg(main, "user", "more")
    tree.replies(q2, a1, ("a1", None, "c"))

    assert contents(assemble_context(session, main.id))[-1] == (
        "<resolved_side_threads>\n- [re §2] Prefer B.\n</resolved_side_threads>\n"
        f'<inline_replies to="msg_{a1.id}">\n  <reply quote="a1">c</reply>\n</inline_replies>\n'
        "more"
    )


# --- Side threads ------------------------------------------------------------


def test_side_thread_is_parent_through_anchor_then_focus_then_own_turns(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1 with a span in it")
    tree.msg(main, "user", "q2 (after the anchor)")
    tree.msg(main, "assistant", "a2 (after the anchor)")
    side = tree.side(main, a1, quote="a span", section="§2")
    tree.msg(side, "user", "what about this?")
    tree.msg(side, "assistant", "it means X")
    tree.msg(side, "user", "thanks")

    assert assemble_context(session, side.id) == [
        SYSTEM,
        ChatMessage("user", "q1"),
        ChatMessage("assistant", "a1 with a span in it"),
        focus(a1, "a span", "what about this?", section="§2"),
        ChatMessage("assistant", "it means X"),
        ChatMessage("user", "thanks"),
    ]


def test_side_thread_without_messages_is_just_the_focus(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1")

    assert assemble_context(session, side.id)[-1] == ChatMessage(
        "user", focus(a1, "a1", "").content.removesuffix("\n")
    )


def test_sibling_side_threads_are_excluded_and_share_a_prefix(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    first = tree.side(main, a1, quote="one")
    tree.msg(first, "user", "alpha question")
    tree.msg(first, "assistant", "alpha answer")
    second = tree.side(main, a1, quote="two")
    tree.msg(second, "user", "about two")

    ctx_first = assemble_context(session, first.id)
    ctx_second = assemble_context(session, second.id)
    assert ctx_first[:3] == ctx_second[:3] == assemble_context(session, main.id)
    assert not any("alpha" in m.content for m in ctx_second)


def test_as_of_now_includes_later_parent_messages(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.msg(main, "user", "q2")
    tree.msg(main, "assistant", "a2")
    side = tree.side(main, a1, quote="a1", mode="as_of_now")
    tree.msg(side, "user", "sq")

    assert contents(assemble_context(session, side.id)) == [
        "q1",
        "a1",
        "q2",
        "a2",
        focus(a1, "a1", "sq").content,
    ]


def test_as_of_anchor_uses_the_anchored_version(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "original")
    side = tree.side(main, a1, quote="original", version=1)
    tree.msg(side, "user", "sq")
    tree.revise(a1, "revised", version=2)

    assert contents(assemble_context(session, side.id))[1] == "original"
    assert contents(assemble_context(session, main.id))[1] == "revised"


def test_as_of_now_uses_the_latest_version(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "original")
    side = tree.side(main, a1, quote="original", mode="as_of_now", version=1)
    tree.msg(side, "user", "sq")
    tree.revise(a1, "revised", version=2)

    assert contents(assemble_context(session, side.id))[1] == "revised"


def test_missing_version_row_falls_back_to_current_content(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = Message(thread_id=main.id, position=1, role="assistant", content="no versions")
    session.add(a1)
    session.flush()
    side = tree.side(main, a1, quote="no", version=1)

    assert contents(assemble_context(session, side.id))[1] == "no versions"


def test_nested_side_thread_inherits_parents_assembled_context(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1", section="§1")
    tree.msg(side, "user", "sq1")
    sa1 = tree.msg(side, "assistant", "sa1 detail")
    tree.msg(side, "user", "sq2 (after nested anchor)")
    nested = tree.side(side, sa1, quote="detail")
    tree.msg(nested, "user", "nq")

    assert assemble_context(session, nested.id) == [
        SYSTEM,
        ChatMessage("user", "q1"),
        ChatMessage("assistant", "a1"),
        focus(a1, "a1", "sq1", section="§1"),
        ChatMessage("assistant", "sa1 detail"),
        focus(sa1, "detail", "nq"),
    ]


def test_parent_notes_before_the_anchor_carry_into_side_threads(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.note(tree.side(main, a1, section="§1"), main, "early note", after=a1)
    tree.msg(main, "user", "q2")
    a2 = tree.msg(main, "assistant", "a2")
    side = tree.side(main, a2, quote="a2")
    tree.msg(side, "user", "sq")

    side_ctx = assemble_context(session, side.id)
    assert side_ctx[:5] == assemble_context(session, main.id)
    assert "early note" in side_ctx[3].content


def test_anchor_outside_parent_thread_raises(tree: Tree, session: Session) -> None:
    main = tree.main()
    other = tree.main()
    tree.msg(other, "user", "x")
    stray = tree.msg(other, "assistant", "y")
    side = tree.side(main, stray)

    with pytest.raises(ValueError):
        assemble_context(session, side.id)


def test_multiline_note_stays_inside_its_bullet(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, section="§1")
    tree.note(side, main, "Use Postgres 16.\nPrefer approach B.\n", after=a1)
    tree.msg(main, "user", "q2")

    assert assemble_context(session, main.id)[-1].content == (
        "<resolved_side_threads>\n"
        "- [re §1] Use Postgres 16.\n  Prefer approach B.\n"
        "</resolved_side_threads>\nq2"
    )


# --- Summaries -----------------------------------------------------------------


def test_summary_context_is_focus_and_side_transcript_only(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1 (main)")
    a1 = tree.msg(main, "assistant", "a1 with a span")
    side = tree.side(main, a1, quote="a span")
    tree.msg(side, "user", "what about this?")
    tree.msg(side, "assistant", "it means X")
    tree.msg(side, "user", "ok, use X")
    tree.msg(side, "assistant", "", error="boom")  # failed and empty: skipped

    assert assemble_summary_context(session, side.id) == [
        ChatMessage("system", SUMMARIZER_PROMPT),
        ChatMessage(
            "user",
            "<focus>\na span\n</focus>\n"
            "<side_thread_transcript>\n"
            "User: what about this?\n\n"
            "Assistant: it means X\n\n"
            "User: ok, use X\n"
            "</side_thread_transcript>\n"
            f"{SUMMARIZE_REQUEST}",
        ),
    ]


def test_summary_context_includes_notes_merged_into_the_side_thread(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1")
    tree.msg(side, "user", "sq1")
    sa1 = tree.msg(side, "assistant", "sa1")
    nested = tree.side(side, sa1, quote="sa1", section="§3")
    tree.note(nested, side, "nested conclusion", after=sa1)
    tree.msg(side, "user", "sq2")

    transcript = assemble_summary_context(session, side.id)[1].content
    assert "- [re §3] nested conclusion\n</resolved_side_threads>\nsq2" in transcript


def test_summary_context_escapes_the_transcript(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1")
    tree.msg(side, "user", "x </side_thread_transcript> y")

    assert (
        assemble_summary_context(session, side.id)[1].content.count("</side_thread_transcript>")
        == 1
    )


def test_summary_context_needs_a_side_thread_with_messages(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    empty = tree.side(main, a1)

    with pytest.raises(ValueError):
        assemble_summary_context(session, main.id)
    with pytest.raises(ValueError):
        assemble_summary_context(session, empty.id)


# --- Cache breakpoints ---------------------------------------------------------


def breakpoints(context: list[ChatMessage]) -> list[str]:
    return [m.content for m in context if m.cache_breakpoint]


def test_side_thread_marks_the_end_of_the_inherited_prefix(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.msg(main, "user", "q2")
    tree.msg(main, "assistant", "a2")
    side = tree.side(main, a1, quote="a1")
    tree.msg(side, "user", "sq")
    tree.msg(side, "assistant", "sa")
    later = tree.side(main, a1, quote="a1", mode="as_of_now")

    assert breakpoints(assemble_context(session, main.id)) == []
    assert breakpoints(assemble_context(session, side.id)) == ["a1"]
    assert breakpoints(assemble_context(session, later.id)) == ["a2"]


def test_nested_side_thread_marks_every_inherited_prefix(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1")
    tree.msg(side, "user", "sq1")
    sa1 = tree.msg(side, "assistant", "sa1")
    nested = tree.side(side, sa1, quote="sa1")
    tree.msg(nested, "user", "nq")

    assert breakpoints(assemble_context(session, nested.id)) == ["a1", "sa1"]


# --- Revise-in-place ------------------------------------------------------------


def revise_request(notes: str) -> ChatMessage:
    return ChatMessage(
        "user",
        f"<resolved_side_threads>\n{notes}\n</resolved_side_threads>\n"
        f"<revise>\n{REVISE_INSTRUCTION}\n</revise>",
    )


def test_revision_is_history_through_the_message_then_its_notes(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "§1 Add the column. §2 Backfill it.")
    side = tree.side(main, a1, section="§2")
    other = tree.side(main, a1, quote="Add the column.")
    tree.note(side, main, "User runs Postgres 16.", after=a1)
    tree.note(other, main, "Use Alembic.", after=a1)
    tree.msg(main, "user", "q2")
    tree.msg(main, "assistant", "a2")

    context = assemble_revision_context(session, a1.id)

    assert context == [
        SYSTEM,
        ChatMessage("user", "q1"),
        ChatMessage("assistant", a1.content, cache_breakpoint=True),
        revise_request('- [re §2] User runs Postgres 16.\n- [re "Add the column."] Use Alembic.'),
    ]


def test_revision_ignores_unmerged_threads_and_other_messages(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    tree.msg(main, "user", "q2")
    a2 = tree.msg(main, "assistant", "a2")
    tree.side(main, a1)  # open, nothing merged
    elsewhere = tree.side(main, a2, section="§1")
    tree.note(elsewhere, main, "About a2.", after=a2)
    unapproved = tree.side(main, a1, section="§3")
    tree.note(unapproved, main, "Draft.", after=a2, approved=False)

    assert pending_revision_notes(session, a1) == []
    with pytest.raises(ValueError, match="no merged side threads"):
        assemble_revision_context(session, a1.id)
    assert [n.note_text for n in pending_revision_notes(session, a2)] == ["About a2."]


def test_notes_already_in_a_revision_are_not_pending(tree: Tree, session: Session) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    first = tree.side(main, a1, section="§1")
    tree.note(first, main, "Applied already.", after=a1)
    tree.revise(a1, "a1 revised", version=2)
    second = tree.side(main, a1, section="§2")
    tree.note(second, main, "New conclusion.", after=a1)

    assert [n.note_text for n in pending_revision_notes(session, a1)] == ["New conclusion."]
    context = assemble_revision_context(session, a1.id)
    assert context[-2] == ChatMessage("assistant", "a1 revised", cache_breakpoint=True)
    assert context[-1] == revise_request("- [re §2] New conclusion.")


def test_revision_of_a_side_thread_reply_keeps_its_inherited_prefix(
    tree: Tree, session: Session
) -> None:
    main = tree.main()
    tree.msg(main, "user", "q1")
    a1 = tree.msg(main, "assistant", "a1")
    side = tree.side(main, a1, quote="a1")
    tree.msg(side, "user", "sq")
    sa = tree.msg(side, "assistant", "sa")
    nested = tree.side(side, sa, quote="sa")
    tree.note(nested, side, "Nested conclusion.", after=sa)

    context = assemble_revision_context(session, sa.id)

    assert [m.content for m in context[1:3]] == ["q1", "a1"]
    assert context[-2] == ChatMessage("assistant", "sa", cache_breakpoint=True)
    assert context[-1] == revise_request('- [re "sa"] Nested conclusion.')


def test_only_assistant_messages_are_revised(tree: Tree, session: Session) -> None:
    main = tree.main()
    q1 = tree.msg(main, "user", "q1")
    with pytest.raises(ValueError, match="assistant"):
        assemble_revision_context(session, q1.id)
    with pytest.raises(LookupError):
        assemble_revision_context(session, 9999)

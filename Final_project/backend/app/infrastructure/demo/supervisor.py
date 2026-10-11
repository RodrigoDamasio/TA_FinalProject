"""Demo Supervisor: researcher → writer → reviewer, then FINAL = the latest Writer draft.
After human feedback it asks the writer once more. Owner: SUPERVISOR."""

import re

from .common import question, section

ORDER = ("researcher", "writer", "reviewer")
_RESULT = re.compile(r"Result from (\w+)(?: \(failed\))?:")
_WRITER_BLOCK = re.compile(r'worker="writer">\n(.*?)\n</data id=', re.DOTALL)


def respond(prompt: str) -> str:
    if "You have no decisions left" in prompt:
        return final(prompt, "No decisions left.")
    conversation = section(prompt, "Conversation so far", until="Decisions left")
    if "Human reviewer feedback" in conversation:
        after = conversation.rsplit("Human reviewer feedback", 1)[1]
        if "Result from writer" not in after:
            return (
                "REASON: The reviewer asked for changes.\nDELEGATE: writer\n"
                "TASK: Revise the draft to address the human reviewer's feedback."
            )
        return final(prompt, "The revised draft addresses the feedback.")
    done = set(_RESULT.findall(conversation))
    for worker in ORDER:
        if worker not in done:
            task = {
                "researcher": f"Find the key facts about: {question(prompt)}",
                "writer": "Write the report from the research findings and evidence.",
                "reviewer": "Review the latest draft against the question and the findings.",
            }[worker]
            return f"REASON: Next step is the {worker}.\nDELEGATE: {worker}\nTASK: {task}"
    return final(prompt, "The draft was reviewed and approved.")


def final(prompt: str, reason: str) -> str:
    drafts = _WRITER_BLOCK.findall(section(prompt, "Conversation so far", until="Decisions left"))
    body = (
        drafts[-1].strip()
        if drafts
        else (
            f"# {question(prompt) or 'Research report'}\n\n"
            "Demo mode (LLM_MODE=fake, no Gemini call): no draft was written.\n\n"
            "## Notes\n\nThe scripted Supervisor finished without a Writer draft."
        )
    )
    return f"REASON: {reason}\nFINAL:\n{body}"

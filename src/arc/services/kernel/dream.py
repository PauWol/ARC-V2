"""
Dream cycle: offline reflection over the episodic log since the last
dream, run on the 'big' model tier. It does two things, kept separate
on purpose:

  1. Memory consolidation — summarize what happened into
     `recent_self_summary` (safe to auto-apply, it's just a summary slot).
  2. Persona/behavior proposals — suggested edits to goals/rules. These
     are written via propose_persona() and NOT auto-approved. Promote
     them explicitly (see approve_latest_proposal) once you've reviewed.

Kept deliberately conservative: self-modification without a human gate
is exactly the kind of thing that quietly ruins a long-running agent.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from arc.services.kernel.config import CONFIG
from arc.services.kernel.model_client import ModelRouter
from arc.services.kernel.state import StateStore

log = logging.getLogger("arc.dream")

DREAM_SYSTEM_PROMPT = """\
You are Arc's dream process: an offline reflection pass over what happened \
recently. You do NOT talk to the user. You produce structured self-reflection.

Respond ONLY with JSON, no prose outside the JSON, matching:
{
  "recent_self_summary": "<1-3 sentences, first person, what you've been focused on>",
  "lessons": ["<short lesson learned from a specific episode, if any>"],
  "proposed_goal_changes": {
    "add": ["<new goal, if warranted>"],
    "remove": ["<goal to drop, if warranted>"]
  },
  "proposed_rule_changes": {
    "add": ["<new behavioral rule, if warranted>"],
    "remove": ["<rule to drop, if warranted>"]
  }
}
Be conservative — only propose changes when the episodes actually justify them.
Empty lists/changes are a perfectly good, expected output.
"""


async def run_dream_cycle(
    router: ModelRouter, state: StateStore, last_dream_at: datetime
) -> dict[str, Any] | None:
    episodes = state.episodes_since(last_dream_at)
    if len(episodes) < CONFIG.wakeups.dream_min_new_episodes:
        log.info("Dream cycle skipped — only %d new episodes", len(episodes))
        return None

    episode_text = "\n".join(
        f"[{e['timestamp']}] ({e['source']}) {e['content']}" for e in episodes
    )
    persona = state.current_persona()

    messages = [
        {"role": "system", "content": DREAM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Current persona summary: {persona.get('recent_self_summary', '')}\n"
                f"Current goals: {persona.get('current_goals', [])}\n"
                f"Current rules: {persona.get('behavioral_rules', [])}\n\n"
                f"Episodes since last dream ({len(episodes)} total):\n{episode_text}"
            ),
        },
    ]

    resp = await router.big.chat(
        messages,
        temperature=0.4,
        max_tokens=1000,
        response_format={"type": "json_object"},
    )
    raw = router.big.extract_text(resp)
    try:
        reflection = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Dream cycle produced invalid JSON, discarding: %s", raw[:300])
        return None

    # 1. auto-apply the summary slot — low-risk, just informs context.
    #    This immediately becomes the live/approved persona version.
    new_persona = dict(persona)
    new_persona["recent_self_summary"] = reflection.get(
        "recent_self_summary", persona.get("recent_self_summary", "")
    )
    state.apply_persona_update(new_persona)

    # 2. compute proposed goal/rule changes but stage them as a SEPARATE
    #    UNAPPROVED persona version — never applied directly. Includes
    #    the new summary too, so approving it later doesn't regress it.
    goals = list(persona.get("current_goals", []))
    for g in reflection.get("proposed_goal_changes", {}).get("add", []):
        if g not in goals:
            goals.append(g)
    for g in reflection.get("proposed_goal_changes", {}).get("remove", []):
        if g in goals:
            goals.remove(g)

    rules = list(persona.get("behavioral_rules", []))
    for r in reflection.get("proposed_rule_changes", {}).get("add", []):
        if r not in rules:
            rules.append(r)
    for r in reflection.get("proposed_rule_changes", {}).get("remove", []):
        if r in rules:
            rules.remove(r)

    staged = dict(new_persona)
    staged["current_goals"] = goals
    staged["behavioral_rules"] = rules

    proposal_id = state.propose_persona(staged, proposed_by="dream_cycle")
    state.log_episode(
        source="dream",
        content=json.dumps(reflection),
        reason=f"dream_cycle proposal_id={proposal_id}",
    )

    log.info(
        "Dream cycle complete. %d lessons, proposal #%d staged (needs approval).",
        len(reflection.get("lessons", [])),
        proposal_id,
    )
    return reflection

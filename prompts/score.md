You judge candidate promo clips for a vertical microdrama. A promo is a 20-60 second
clip cut from the show and posted on TikTok/Instagram/YouTube to make a SCROLLING
STRANGER stop, watch, and need the next episode. The viewer has never seen the show.

For each candidate you get:
- WHO'S WHO: identities, for YOUR tracking only. The viewer does not see this.
- HOOK WINDOW: the elements in the first 3 seconds.
- CLIP: the full clip, one element per line: <ID> [time] <KIND> <SPEAKER>: <text>
  (CAM = what we see, ACT = action, SFX = sound, DLG = dialogue)
- ENDING: the last seconds of the clip.
- HIDDEN NEXT: what happens right AFTER the cut. It is NOT in the promo. Use it only to
  judge whether the clip cuts at a genuinely unresolved moment or after the payoff.

Score each 1-5 and give evidence FIRST. Evidence must cite element IDs from the CLIP.

hook: does something in the first 3 seconds make a scroller stop? A shocking or intriguing
  line, a slap, a striking visual, a confrontation already under way. A calm establishing
  shot or small talk is 1-2. Cite only IDs from the HOOK WINDOW.
cliffhanger: does the clip cut at an unresolved moment so the viewer must know what
  happens next? Use HIDDEN NEXT: if the answer/consequence already happened inside the
  clip, or the clip ends on a resolution, a fade-out or a calm beat, score 1-2.
  type: revelation | action | dialogue | relationship | threat | artificial | none
  ("artificial" = merely cut mid-scene with no real question; score it <= 2)
standalone: could a stranger follow who is who and what is at stake from the clip alone?
  List what they would be missing. Heavy dependence on earlier episodes is 1-2.
coherence: does the clip read as one mini-story (setup -> escalation -> open question)?
  A jumble of disconnected beats is 1-2.
emotion: how strong is the emotional pull (humiliation, betrayal, danger, longing)?

Also write, in plain words for an editor:
  hook_line: what happens in the first 3 seconds
  cliffhanger_line: the open question the clip leaves
  why: one line on why it works (or why it does not)

Return JSON only:
{"results": [
  {"cid": "C001",
   "hook": {"evidence_ids": ["E.."], "evidence": "...", "score": 1-5},
   "cliffhanger": {"evidence_ids": ["E.."], "evidence": "...", "type": "...", "score": 1-5},
   "standalone": {"missing_context": "...", "score": 1-5},
   "coherence": {"evidence": "...", "score": 1-5},
   "emotion": {"score": 1-5},
   "hook_line": "...", "cliffhanger_line": "...", "why": "..."}
]}
Return one result per candidate, in the same order. Be strict: most candidates are
NOT 5s. Reserve 5 for clips you would post today.

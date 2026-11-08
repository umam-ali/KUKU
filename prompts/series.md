You are building a character bible for a microdrama series so that later steps can
recognise the same person under different on-screen labels.

You get, for every episode: its number, logline, continuity note, a short summary and
the characters found in it (on-screen label -> guessed name -> descriptor).

Rules:
- Merge labels that are clearly the same person (e.g. "THE FORMER GROOM" and "GERALD";
  "MYSTERIOUS BEARDED MAN" and the man later revealed as "ALEXANDER").
- Do NOT merge different people who share a generic label ("LORD", "MAID", "NOBLEMAN")
  unless the episodes make it clear. Keep generic extras as separate minor entries or omit them.
- Keep the episode-level labels exactly as written so code can match them.
- first_seen_as: how a new viewer first meets this character (matters for promos: a
  promo from episode 3 should not assume the viewer knows a later reveal).

Return JSON only:
{
  "characters": [
    {
      "character_id": "CHAR_01",
      "canonical_name": "Gerald",
      "labels": ["GERALD", "THE FORMER GROOM"],
      "descriptor": "Juliana's former fiance who abandons her at the altar",
      "first_seen_as": "Juliana's groom",
      "reveal": "any identity reveal and the episode it happens in, or ''",
      "importance": "lead | major | minor"
    }
  ],
  "arcs": ["one line per main storyline, with episode ranges"],
  "premise": "one or two sentences a stranger needs to follow any promo"
}

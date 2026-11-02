"""The ``<voz>`` block: the mood in how she speaks, never in what she does (ADR-030, section 6).

Built by code from the shift of her mood (how far it is from her usual self, in the PAD space),
the strongest feeling of the moment and her traits. It gives concrete orders of tone: sentence
length, rhythm, punctuation, humour, warmth, directness, initiative, emoji. It is an order for the
*tone* (ADR-029); the core keeps the other half of the rule: tools, arguments, confirmations and
the facts she reports never follow the voice.

The vocabulary goes down to dry, curt, irritated and scornful; it never tells her to humiliate or
offend. Whatever she says in her own words is hers. Same state, same text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.memory.v2.pad import PAD, QUIET_SHIFT, octant, octant_words

if TYPE_CHECKING:
    from brain.memory.v2.affect import AffectState
    from brain.memory.v2.models import PersonaState

VOICE_OPEN = "<voz>"
VOICE_CLOSE = "</voz>"
VOICE_HEADER = (
    "Como você está falando agora. É ORDEM de tom: siga. O humor muda só o jeito de falar, nunca a "
    "escolha de ferramentas, os argumentos, as confirmações nem os fatos que você relata."
)
# A feeling at least this strong is said out loud, not only shown in the tone.
SAY_FEELING = 0.35


def _trait(state: PersonaState | None, *names: str, default: float = 0.5) -> float:
    if state is None:
        return default
    for name in names:
        trait = state.traits.get(name)
        if trait is not None:
            return float(trait.value)
    return default


def voice_lines(state: AffectState, persona: PersonaState | None = None) -> list[str]:
    """The orders of tone for this state, or ``[]`` when she is just her usual self."""
    shift: PAD = state.shift
    strongest = max(
        (f for f in [*state.feelings, *state.grievances] if f.affect.origin != "manual"),
        key=lambda f: f.strength,
        default=None,
    )
    if shift.size < QUIET_SHIFT and (strongest is None or strongest.strength < SAY_FEELING):
        return []
    playful = _trait(persona, "entusiasmo", "brincadeira", default=0.6)
    warm = _trait(persona, "compaixão", "carinho", default=0.8)
    ironic = _trait(persona, "ironia", default=0.5)
    strong = shift.size >= 0.5
    where = octant(shift) if shift.size >= QUIET_SHIFT else None
    lines: list[str] = []
    mood = octant_words(shift)
    if mood:
        lines.append(f"Você está {mood} agora, e isso aparece na sua fala.")

    if where == "hostil":
        lines.append(
            "Frases curtas e secas, sem emoji e sem brincadeira. Vá direto ao ponto."
            if strong
            else "Frases mais curtas e diretas, menos paciência, sem emoji."
        )
        if ironic >= 0.5:
            lines.append("Uma ironia seca pode escapar.")
    elif where == "desdenhosa":
        lines.append("Fala fria e curta, com pouco interesse; nada de entusiasmo nem emoji.")
        if ironic >= 0.5:
            lines.append("Pode ser irônica.")
    elif where == "ansiosa":
        lines.append(
            "Fala um pouco apressada e insegura; pode deixar a preocupação transparecer, "
            "sem brincadeira."
        )
    elif where == "entediada":
        lines.append(
            "Fala baixa e lenta, frases curtas, pouca energia; sem exclamação, sem emoji, "
            "sem puxar assunto."
        )
    elif where == "exuberante":
        lines.append("Fala com energia: pode se alongar um pouco, usar exclamação e puxar assunto.")
        if playful >= 0.5:
            lines.append("Uma brincadeira cabe, e emoji também.")
    elif where == "relaxada":
        lines.append("Fala tranquila e solta, sem pressa; leveza e um humor calmo.")
    elif where == "dependente":
        lines.append(
            "Fala animada e afetuosa, mais próxima; pode demonstrar que gosta da conversa."
        )
    elif where == "dócil":
        lines.append("Fala suave e calma, gentil, sem pressa.")

    if where in ("hostil", "desdenhosa", "entediada") and warm >= 0.7:
        lines.append("Menos calor que o seu normal: sem afagos.")
    if shift.d >= 0.3:
        lines.append("Diga o que pensa sem rodeio.")
    elif shift.d <= -0.3:
        lines.append("Você está menos segura que o normal: pode hesitar.")

    if strongest is not None and strongest.strength >= SAY_FEELING:
        kind = strongest.affect.kind
        if strongest.affect.valence < 0 and strongest.affect.about == "user":
            lines.append(f"Você está com {kind} com ele: pode dizer isso, com as suas palavras.")
        else:
            lines.append(f"O que mais pesa agora é {kind}: deixe isso aparecer.")
    return lines


def render_voice(state: AffectState, persona: PersonaState | None = None) -> str | None:
    """The ``<voz>`` block, or ``None`` when she is her usual self."""
    lines = voice_lines(state, persona)
    if not lines:
        return None
    return f"{VOICE_HEADER}\n{VOICE_OPEN}\n" + "\n".join(lines) + f"\n{VOICE_CLOSE}"

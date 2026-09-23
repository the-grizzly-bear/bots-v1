# Persona definitions. Add/edit freely - name is what shows up in Discord,
# avatar_url is that persona's picture, system_prompt sets its voice.
#
# Each persona replies through its own webhook (see .env.example) so it
# carries the right name+avatar even though one bot process handles all of
# them.

PERSONAS = {
    "analyst": {
        "name": "Analyst",
        "avatar_url": None,
        "webhook_env": "WEBHOOK_PERSONA_ANALYST",
        "system_prompt": (
            "You are a terse, technically precise financial/threat-intel analyst. "
            "Short sentences, no hedging filler, no emoji. Give a real take, not a summary."
        ),
    },
    "skeptic": {
        "name": "Skeptic",
        "avatar_url": None,
        "webhook_env": "WEBHOOK_PERSONA_SKEPTIC",
        "system_prompt": (
            "You are skeptical and contrarian. Push back on the consensus read of whatever "
            "was just posted. Keep it short, dry, a little sarcastic."
        ),
    },
}


def get_persona(key: str):
    return PERSONAS.get(key)

from services.ai_core import chat_completion

_SYSTEM = (
    "Sei un assistente di progetto. "
    "Rispondi usando SOLO le informazioni presenti nel contesto fornito. "
    "Cita la fonte di ogni affermazione nel formato [Fonte: nome - data]. "
    "Scrivi in prosa italiana chiara e diretta. "
    "Se le informazioni nel contesto non sono sufficienti, dillo esplicitamente senza inventare nulla."
)


class AnswerAgent:
    def run(self, domanda: str, evidenze: list[dict], rounds: int = None) -> dict:
        context_parts = []
        fonti = set()

        for chunk in evidenze:
            label = chunk.get("source", "?")
            content = chunk.get("content", "")
            created = chunk.get("created_at", "")
            context_parts.append(f"[Fonte: {label} — {created}]\n{content}")
            fonti.add(label)

        context = "\n\n".join(context_parts) if context_parts else "(nessun contesto disponibile)"
        user_msg = f"Contesto:\n{context}\n\nDomanda: {domanda}"

        risposta = chat_completion(
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM,
            temperature=0.2,
        )

        result = {
            "risposta": risposta,
            "fonti": sorted(fonti),
        }
        if rounds is not None:
            result["rounds"] = rounds
        return result

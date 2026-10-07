import json
import re
import sys
from services.ai_core import chat_completion
from agents.retrieval import RetrievalAgent
from agents.answer import AnswerAgent

MAX_ROUND = 5

_SYSTEM_JUDGE = (
    "Sei un valutatore di evidenze per un sistema RAG. "
    "Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza markdown, senza testo aggiuntivo."
)

_PROMPT_JUDGE = """\
Domanda originale: "{domanda}"

Evidenze raccolte finora ({n_chunks} chunk da {n_fonti} fonti):
{evidenze_summary}

Decidi se le evidenze sono sufficienti per rispondere in modo completo e accurato, oppure se serve un'altra ricerca mirata su un aspetto specifico non ancora coperto (es. un nome, una data, un dettaglio collegato a quanto già trovato).

Rispondi con questo JSON esatto:
{{
  "sufficiente": true,
  "prossima_query": null
}}

Regole:
- Se le evidenze bastano: "sufficiente": true, "prossima_query": null
- Se manca qualcosa: "sufficiente": false, "prossima_query": "la query di ricerca mirata da eseguire"\
"""


def _parse_judge(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


class AgenticRetrievalAgent:
    def esegui(self, domanda: str, project_id: str) -> dict:
        retriever = RetrievalAgent()
        answer_agent = AnswerAgent()

        evidenze: list[dict] = []
        seen: set[tuple] = set()
        query_corrente = domanda
        rounds = 0

        for round_num in range(1, MAX_ROUND + 1):
            rounds = round_num
            nuovi = retriever.cerca(query_corrente, project_id)

            for chunk in nuovi:
                key = (chunk.get("source", ""), chunk.get("content", "")[:100])
                if key not in seen:
                    seen.add(key)
                    evidenze.append(chunk)

            verdetto = self._giudica(domanda, evidenze)

            if verdetto.get("sufficiente") or not verdetto.get("prossima_query"):
                break

            query_corrente = verdetto["prossima_query"]
            print(f"[AgenticRAG] Round {round_num} → prossima query: {query_corrente!r}", file=sys.stderr)

        print(f"[AgenticRAG] Completato in {rounds} round(s), {len(evidenze)} chunk totali", file=sys.stderr)
        return answer_agent.run(domanda, evidenze, rounds=rounds)

    def _giudica(self, domanda: str, evidenze: list[dict]) -> dict:
        fonti = set(c.get("source", "?") for c in evidenze)
        evidenze_summary = "\n".join(
            f"- [{c.get('source', '?')}] {c.get('content', '')[:150]}..."
            for c in evidenze[:12]
        )
        try:
            raw = chat_completion(
                messages=[{
                    "role": "user",
                    "content": _PROMPT_JUDGE.format(
                        domanda=domanda,
                        n_chunks=len(evidenze),
                        n_fonti=len(fonti),
                        evidenze_summary=evidenze_summary,
                    ),
                }],
                system=_SYSTEM_JUDGE,
                temperature=0.0,
            )
            return _parse_judge(raw)
        except Exception as e:
            print(f"[AgenticRAG] _giudica errore (fail-safe): {e}", file=sys.stderr)
            return {"sufficiente": True, "prossima_query": None}

import os
from services.ai_core import chat_completion
from services.hana import get_connection, is_sqlite
from agents.retrieval import RetrievalAgent
from agents.answer import AnswerAgent
from agents.scope_guardian import ScopeGuardian

_SYSTEM_CLASSIFY = """\
Classifica l'intent del messaggio dell'utente. Rispondi con UNA SOLA parola tra: GENERALE, TRASVERSALE, DECISIONI, SCOPE.

SCOPE: scegli SCOPE SOLO se l'utente sta chiedendo di verificare se una ATTIVITÀ SPECIFICA o RICHIESTA CONCRETA rientra o meno nel perimetro contrattuale. Esempi: "È nel contratto fare X?", "Rientra nello scope la richiesta di Y?", "Possiamo fare Z senza una change request?".
NON è SCOPE: domande sul contenuto del contratto, su cosa dice il contratto, sullo scopo del contratto.

DECISIONI: scegli DECISIONI se l'utente chiede delle decisioni prese, del decision log, di scelte approvate, o di chi ha deciso cosa.

TRASVERSALE: scegli TRASVERSALE se l'utente chiede una panoramica generale, un riassunto trasversale di più documenti, chi ha partecipato a cosa, o domande del tipo "di cosa parlano i documenti".

GENERALE: qualsiasi altra domanda specifica su contenuti, fatti, date, persone, clausole, documenti singoli.\
"""


class Orchestrator:
    def smista(self, messaggio: str, project_id: str = None, modalita: str = "standard") -> dict:
        if project_id is None:
            project_id = os.getenv("PROJECT_ID", "demo")

        intent_raw = chat_completion(
            messages=[{"role": "user", "content": messaggio}],
            system=_SYSTEM_CLASSIFY,
            temperature=0.0,
        ).strip().upper()

        # ── SCOPE ────────────────────────────────────────────────────────────
        if "SCOPE" in intent_raw:
            result = ScopeGuardian().check_query(messaggio, project_id)
            if result.get("warning"):
                risposta = f"ℹ️ {result['warning']}"
            elif result.get("fuori_scope"):
                elementi = result.get("elementi", [])
                confidenza = result.get("confidenza", "")
                clausole = result.get("clausole_rilevanti", [])
                risposta = (
                    f"⚠️ Questa richiesta sembra **FUORI SCOPE** (confidenza: {confidenza}).\n\n"
                    f"**Elementi fuori perimetro:** {', '.join(elementi)}\n\n"
                    f"**Motivazione:** {result.get('motivazione', '')}"
                )
                if clausole:
                    risposta += f"\n\n**Clausole di riferimento:** {'; '.join(clausole)}"
            else:
                confidenza = result.get("confidenza", "")
                risposta = (
                    f"✅ La richiesta sembra essere **nel perimetro contrattuale** (confidenza: {confidenza}).\n\n"
                    f"**Motivazione:** {result.get('motivazione', '')}"
                )
            return {"tipo": "SCOPE", "risposta": risposta, "fonti": ["CONTRATTO"], "raw": result}

        # ── DECISIONI ─────────────────────────────────────────────────────────
        if "DECISIONI" in intent_raw:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                limit = "LIMIT 10" if is_sqlite() else ""
                top = "" if is_sqlite() else "TOP 10"
                cursor.execute(
                    f"SELECT {top} DESCRIZIONE, AUTORE, DATA_DECISIONE, MOTIVAZIONE "
                    f"FROM IPMP_DECISIONS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC {limit}",
                    [project_id],
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
                conn.close()

            if not rows:
                risposta = "Non ho ancora decisioni registrate in questo progetto."
            else:
                lines = []
                for r in rows:
                    lines.append(
                        f"• **{r[0]}** (Autore: {r[1]}, Data: {r[2]})\n  Motivazione: {r[3]}"
                    )
                risposta = "**Decisioni del progetto:**\n\n" + "\n\n".join(lines)
            return {"tipo": "DECISIONI", "risposta": risposta, "fonti": ["IPMP_DECISIONS"], "raw": {}}

        # ── TRASVERSALE ───────────────────────────────────────────────────────
        if "TRASVERSALE" in intent_raw:
            chunks = RetrievalAgent().cerca_contesto(messaggio, project_id)
            result = AnswerAgent().run(messaggio, chunks)
            return {"tipo": "TRASVERSALE", "risposta": result["risposta"], "fonti": result["fonti"], "raw": {}}

        # ── GENERALE: standard o agentic ──────────────────────────────────────
        if modalita == "agentic":
            from agents.agentic_retrieval import AgenticRetrievalAgent
            result = AgenticRetrievalAgent().esegui(messaggio, project_id)
            rounds = result.get("rounds", 1)
            return {
                "tipo": "GENERALE",
                "modalita": "agentic",
                "rounds": rounds,
                "risposta": result["risposta"],
                "fonti": result["fonti"],
                "raw": {},
            }

        # standard
        chunks = RetrievalAgent().cerca(messaggio, project_id)
        result = AnswerAgent().run(messaggio, chunks)
        return {"tipo": "GENERALE", "modalita": "standard", "risposta": result["risposta"], "fonti": result["fonti"], "raw": {}}

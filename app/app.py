import os
import sys
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="i-PMP", layout="wide", page_icon="🧠", initial_sidebar_state="expanded")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── SIDEBAR ── */
section[data-testid="stSidebar"] {
    background: #0F0A1E;
    border-right: 1px solid rgba(124,58,237,0.2);
}
/* Colori testo sidebar */
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label { color: #C4B5FD !important; }
/* Nascondi titolo radio */
section[data-testid="stSidebar"] [data-testid="stRadio"] > div:first-child { display:none; }
/* Stile opzioni radio */
section[data-testid="stSidebar"] [data-baseweb="radio"] {
    background: transparent;
    border-radius: 8px;
    padding: 4px 8px;
    margin: 2px 0;
}
section[data-testid="stSidebar"] [data-baseweb="radio"]:hover {
    background: rgba(124,58,237,0.15) !important;
}
section[data-testid="stSidebar"] [data-baseweb="radio"] [data-checked="true"] ~ div,
section[data-testid="stSidebar"] [data-baseweb="radio"][aria-checked="true"] {
    background: rgba(124,58,237,0.25) !important;
}

/* ── MAIN BG ── */
.main .block-container {
    padding: 2rem 2.5rem;
    max-width: 1200px;
}
.stApp { background: #F8F7FF; }

/* ── PAGE HEADER ── */
.page-hero {
    background: linear-gradient(135deg, #1E0A3C 0%, #4C1D95 50%, #7C3AED 100%);
    border-radius: 20px;
    padding: 36px 40px;
    margin-bottom: 32px;
    position: relative;
    overflow: hidden;
}
.page-hero::before {
    content: '';
    position: absolute;
    top: -60px; right: -60px;
    width: 220px; height: 220px;
    background: rgba(255,255,255,0.04);
    border-radius: 50%;
}
.page-hero::after {
    content: '';
    position: absolute;
    bottom: -40px; right: 80px;
    width: 140px; height: 140px;
    background: rgba(255,255,255,0.03);
    border-radius: 50%;
}
.page-hero h1 {
    color: white !important;
    font-size: 2rem;
    font-weight: 800;
    margin: 0 0 6px 0;
    letter-spacing: -0.5px;
}
.page-hero p { color: #C4B5FD; margin: 0; font-size: 0.95rem; }
.page-hero .hero-tag {
    display: inline-block;
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.15);
    color: #DDD6FE;
    border-radius: 20px;
    padding: 3px 12px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    margin-bottom: 12px;
}

/* ── CARD ── */
.card {
    background: white;
    border: 1px solid #EDE9FE;
    border-radius: 16px;
    padding: 24px 28px;
    box-shadow: 0 2px 12px rgba(109,40,217,.05);
    margin-bottom: 16px;
}
.card-dark {
    background: linear-gradient(135deg, #1E0A3C, #2E1065);
    border: 1px solid rgba(124,58,237,0.3);
    border-radius: 16px;
    padding: 24px 28px;
    margin-bottom: 16px;
    color: white;
}
.section-label {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .12em;
    color: #7C3AED;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
}
.section-label::after {
    content: '';
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, #EDE9FE, transparent);
}

/* ── KPI TILES ── */
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
.kpi-tile {
    background: white;
    border-radius: 14px;
    padding: 20px 22px;
    border: 1px solid #EDE9FE;
    box-shadow: 0 2px 8px rgba(109,40,217,.04);
    position: relative;
    overflow: hidden;
}
.kpi-tile::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, #7C3AED, #A78BFA);
    border-radius: 14px 14px 0 0;
}
.kpi-icon { font-size: 1.6rem; margin-bottom: 8px; }
.kpi-value { font-size: 2rem; font-weight: 800; color: #1E0A3C; line-height: 1; }
.kpi-label { font-size: 0.75rem; color: #9CA3AF; margin-top: 4px; font-weight: 500; }

/* ── BADGE ── */
.badge { display:inline-flex; align-items:center; gap:5px; border-radius:20px; padding:4px 12px; font-size:.78rem; font-weight:600; }
.badge-ok   { background:#ECFDF5; color:#059669; border:1px solid #A7F3D0; }
.badge-warn { background:#FFFBEB; color:#D97706; border:1px solid #FDE68A; }
.badge-err  { background:#FEF2F2; color:#DC2626; border:1px solid #FECACA; }
.badge-info { background:#EDE9FE; color:#6D28D9; border:1px solid #DDD6FE; }

/* ── STEP ── */
.steps-list { display:flex; flex-direction:column; gap:14px; }
.step { display:flex; align-items:flex-start; gap:14px; }
.step-num {
    background: linear-gradient(135deg,#7C3AED,#A855F7);
    color:white; border-radius:50%;
    width:30px; height:30px; min-width:30px;
    display:flex; align-items:center; justify-content:center;
    font-weight:700; font-size:.85rem;
    box-shadow:0 2px 8px rgba(124,58,237,.4);
}
.step-body { padding-top:4px; }
.step-title { font-weight:600; color:#1E0A3C; font-size:.9rem; }
.step-desc  { color:#6B7280; font-size:.82rem; margin-top:2px; }

/* ── CHAT WELCOME ── */
.chat-welcome {
    text-align:center;
    padding:60px 40px;
    background:white;
    border-radius:20px;
    border:1px solid #EDE9FE;
    margin-bottom:24px;
}
.chat-welcome .brain { font-size:4rem; margin-bottom:16px; }
.chat-welcome h2 { color:#1E0A3C; font-weight:800; font-size:1.4rem; margin:0 0 8px; }
.chat-welcome p { color:#6B7280; font-size:.9rem; max-width:420px; margin:0 auto 20px; }
.suggestions { display:flex; gap:10px; flex-wrap:wrap; justify-content:center; }
.suggestion-pill {
    background:#F5F3FF; border:1px solid #DDD6FE; color:#6D28D9;
    border-radius:20px; padding:6px 16px; font-size:.82rem; font-weight:500;
    cursor:pointer;
}

/* ── ALERT CARD ── */
.alert-card {
    border-left:4px solid #F59E0B;
    background:#FFFBEB;
    border-radius:0 12px 12px 0;
    padding:14px 18px;
    margin-bottom:12px;
}
.alert-card.handled {
    border-left-color:#10B981;
    background:#ECFDF5;
}

/* ── BUTTONS ── */
.stButton > button {
    border-radius: 10px !important;
    font-weight: 600 !important;
    transition: all .2s !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6D28D9, #7C3AED) !important;
    border: none !important;
    box-shadow: 0 4px 14px rgba(109,40,217,.35) !important;
    color: white !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 6px 20px rgba(109,40,217,.5) !important;
    transform: translateY(-1px) !important;
}
.stButton > button[kind="secondary"] {
    border: 2px solid #7C3AED !important;
    color: #7C3AED !important;
    background: transparent !important;
}

/* ── TABS ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background: #F5F3FF;
    padding: 6px;
    border-radius: 12px;
    border: none;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    font-weight: 600 !important;
    color: #7C3AED !important;
    padding: 8px 18px !important;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    background: white !important;
    color: #4C1D95 !important;
    box-shadow: 0 2px 8px rgba(109,40,217,.12) !important;
}
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* ── FILE UPLOADER ── */
[data-testid="stFileUploader"] {
    border: 2px dashed #DDD6FE !important;
    border-radius: 12px !important;
    padding: 8px !important;
    background: #FAFAFA !important;
}

/* ── INPUTS ── */
.stTextArea textarea, .stTextInput input {
    border-radius: 10px !important;
    border: 1.5px solid #DDD6FE !important;
    font-family: 'Inter', sans-serif !important;
}
.stTextArea textarea:focus, .stTextInput input:focus {
    border-color: #7C3AED !important;
    box-shadow: 0 0 0 3px rgba(124,58,237,.1) !important;
}

/* ── CHAT ── */
[data-testid="stChatMessage"] { border-radius: 14px !important; }

/* ── PROGRESS ── */
.stProgress > div > div {
    background: linear-gradient(90deg, #7C3AED, #A855F7) !important;
    border-radius: 10px !important;
}

/* ── HIDE DEFAULT ── */
footer, #MainMenu { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def api(method: str, path: str, **kwargs):
    url = BACKEND.rstrip("/") + path
    try:
        r = getattr(requests, method)(url, timeout=600, **kwargs)
        r.raise_for_status()
        return r
    except requests.exceptions.ConnectionError:
        st.error("Backend non raggiungibile. Avvia il backend prima.")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"Errore API: {e.response.text}")
        return None


def hero(tag: str, title: str, subtitle: str = ""):
    st.markdown(f"""
    <div class="page-hero">
        <div class="hero-tag">{tag}</div>
        <h1>{title}</h1>
        {"<p>" + subtitle + "</p>" if subtitle else ""}
    </div>""", unsafe_allow_html=True)


def section(label: str):
    st.markdown(f'<div class="section-label">{label}</div>', unsafe_allow_html=True)


def badge(text: str, kind: str = "info"):
    st.markdown(f'<span class="badge badge-{kind}">{text}</span>', unsafe_allow_html=True)


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
NAV_OPTIONS = ["🏠  Dashboard", "💬  Virtual Advisor", "📄  Documenti", "🛡  Scope & CR", "📘  Playbook"]
NAV_KEYS    = ["dashboard", "chat", "docs", "scope", "playbook"]

with st.sidebar:
    st.markdown("""
    <div style="padding:24px 20px 12px;">
        <div style="display:flex;align-items:center;gap:10px;">
            <div style="width:36px;height:36px;background:linear-gradient(135deg,#7C3AED,#A855F7);
                        border-radius:10px;display:flex;align-items:center;justify-content:center;
                        font-size:1.1rem;">🧠</div>
            <div>
                <div style="font-size:1.15rem;font-weight:800;color:white;letter-spacing:-.5px;">i-PMP</div>
                <div style="font-size:.62rem;color:#A78BFA;letter-spacing:.1em;text-transform:uppercase;font-weight:600;">Project Memory</div>
            </div>
        </div>
    </div>
    <hr style="border:none;border-top:1px solid rgba(124,58,237,.25);margin:0 16px 8px;">
    """, unsafe_allow_html=True)

    selected = st.radio("Navigazione", NAV_OPTIONS, label_visibility="collapsed")
    page = NAV_KEYS[NAV_OPTIONS.index(selected)]

    st.markdown("""
    <hr style="border:none;border-top:1px solid rgba(124,58,237,.2);margin:8px 16px 12px;">
    <div style="padding:0 20px 16px;font-size:.7rem;color:#6B7280;text-align:center;line-height:1.8;">
        SAP AI Core · Claude Sonnet<br>
        <span style="color:#7C3AED;font-size:.9rem;">●</span> Sistema attivo
    </div>""", unsafe_allow_html=True)


# ── DASHBOARD ─────────────────────────────────────────────────────────────────
if page == "dashboard":
    hero("🏠 Overview", "Dashboard di Progetto", "Alert attivi e stato della memoria")

    section("🔔 Alert Scope Guardian")
    r = api("get", "/alerts")
    if r:
        alerts = r.json()
        if not alerts:
            st.markdown("""<div class="card" style="text-align:center;padding:24px;">
                <div style="font-size:1.8rem;">✅</div>
                <div style="font-weight:600;color:#059669;margin-top:8px;">Nessun alert aperto</div>
                <div style="color:#9CA3AF;font-size:.8rem;margin-top:4px;">Tutto nel perimetro contrattuale</div>
            </div>""", unsafe_allow_html=True)
        for alert in alerts:
            with st.expander(f"⚠️ {alert['source'][:35]}", expanded=True):
                st.markdown(alert["alert_text"])
                if st.button("✓ Gestito", key=f"handle_{alert['id']}", type="primary"):
                    api("post", f"/alerts/{alert['id']}/handle")
                    st.rerun()


# ── CHAT ──────────────────────────────────────────────────────────────────────
elif page == "chat":
    hero("💬 AI", "Virtual Advisor", "Interroga la memoria del progetto in linguaggio naturale")

    if "deep_thinking" not in st.session_state:
        st.session_state.deep_thinking = False

    col_toolbar_l, col_toolbar_r = st.columns([1, 1])
    with col_toolbar_l:
        if st.button("🗑 Nuova chat"):
            st.session_state.messages = []
            st.rerun()
    with col_toolbar_r:
        deep = st.toggle("🧠 Deep Thinking", value=st.session_state.deep_thinking, help="Attiva la ricerca agentica multi-round. Più accurata ma più lenta.")
        st.session_state.deep_thinking = deep

    modalita = "agentic" if st.session_state.deep_thinking else "standard"

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if not st.session_state.messages:
        st.markdown("""
        <div class="chat-welcome">
            <div class="brain">🧠</div>
            <h2>Come posso aiutarti?</h2>
            <p>Fai domande su verbali, decisioni, email, specifiche tecniche o verifica la conformità contrattuale.</p>
        </div>""", unsafe_allow_html=True)

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            meta = []
            if msg.get("fonti"):
                meta.append("📎 " + " · ".join(msg["fonti"]))
            if msg.get("rounds"):
                meta.append(f"🔄 {msg['rounds']} round")
            if meta:
                st.caption(" | ".join(meta))

    if domanda := st.chat_input("Scrivi la tua domanda..."):
        st.session_state.messages.append({"role": "user", "content": domanda})
        with st.chat_message("user"):
            st.markdown(domanda)
        with st.chat_message("assistant"):
            spinner_msg = "Ricerca agentica in corso (fino a 5 round)..." if modalita == "agentic" else "Ricerca in corso..."
            with st.spinner(spinner_msg):
                r = api("post", "/query", json={"domanda": domanda, "modalita": modalita})
            if r:
                data = r.json()
                risposta = data.get("risposta", "Nessuna risposta.")
                fonti = data.get("fonti", [])
                rounds = data.get("rounds")
                st.markdown(risposta)
                meta = []
                if fonti: meta.append("📎 " + " · ".join(fonti))
                if rounds: meta.append(f"🔄 {rounds} round")
                if meta: st.caption(" | ".join(meta))
                st.session_state.messages.append({"role": "assistant", "content": risposta, "fonti": fonti, "rounds": rounds})


# ── DOCUMENTI ─────────────────────────────────────────────────────────────────
elif page == "docs":
    hero("📄 Knowledge", "Gestione Documenti", "Carica, indicizza e gestisci la knowledge base del progetto")

    tab1, tab2, tab3 = st.tabs(["  📁  Documenti  ", "  📜  Contratto & CR  ", "  🗑  Reset Memoria  "])

    with tab1:
        section("Carica documenti")
        st.caption("Supportati: PDF, DOCX, MSG, PST, PPTX, XLSX, XLS, TXT")
        uploaded_files = st.file_uploader("", type=["pdf","docx","msg","pst","pptx","xlsx","xls","txt"],
                                          key="doc_upload", accept_multiple_files=True, label_visibility="collapsed")
        if uploaded_files and st.button("📤  Processa documenti", type="primary"):
            totale = len(uploaded_files)
            bar = st.progress(0, text=f"Elaborazione 0/{totale}...")
            risultati = []
            for idx, f in enumerate(uploaded_files):
                with st.spinner(f"[{idx+1}/{totale}] {f.name}"):
                    r = api("post", "/ingest", files={"file": (f.name, f.getvalue(), f.type)})
                res = r.json() if r else {}
                res["_nome"] = f.name
                res["_ok"] = bool(r)
                risultati.append(res)
                bar.progress((idx+1)/totale, text=f"Elaborazione {idx+1}/{totale}...")
            bar.empty()

            ok = sum(1 for x in risultati if x["_ok"])
            st.success(f"✅ {ok}/{totale} file processati") if ok == totale else st.warning(f"⚠️ {ok}/{totale} file processati")

            for res in risultati:
                icon = "✅" if res["_ok"] else "❌"
                with st.expander(f"{icon} {res['_nome']}", expanded=False):
                    if res["_ok"]:
                        c1,c2,c3,c4 = st.columns(4)
                        c1.metric("Chunk", res.get("chunks",0))
                        ix = res.get("indexing",{})
                        c2.metric("Indicizzati", ix.get("chunks_indexed","?"))
                        dec = res.get("decisions",{})
                        c3.metric("Decisioni", dec.get("found",0))
                        sc = res.get("scope",{})
                        c4.metric("Scope", "⚠️ Out" if sc.get("fuori_scope") else "✅ OK")

    with tab2:
        col_c, col_cr = st.columns(2, gap="large")
        with col_c:
            section("📜 Contratto baseline")
            st.caption("Necessario per lo Scope Guardian. Puoi caricare SOW + allegati.")
            contracts = st.file_uploader("", type=["pdf","docx","txt"], key="contract_upload",
                                         accept_multiple_files=True, label_visibility="collapsed")
            if contracts and st.button("📜 Carica contratto", type="primary"):
                total = len(contracts)
                bar = st.progress(0)
                tot_chunks = 0
                for idx, c in enumerate(contracts):
                    with st.spinner(f"{c.name}..."):
                        r = api("post", "/load-contract", files={"file": (c.name, c.getvalue(), c.type)})
                    if r:
                        n = r.json().get("chunks_loaded", 0)
                        tot_chunks += n
                        st.markdown(f'<span class="badge badge-ok">✓ {c.name} — {n} chunk</span>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="badge badge-err">✗ {c.name}</span>', unsafe_allow_html=True)
                    bar.progress((idx+1)/total)
                bar.empty()
                st.success(f"Contratto caricato: {tot_chunks} chunk totali")

        with col_cr:
            section("📋 Change Request approvate")
            st.caption("Aggiunge la CR al baseline contrattuale per l'analisi di scope futura.")
            cr_files = st.file_uploader("", type=["pdf","docx","txt"], key="cr_upload",
                                        accept_multiple_files=True, label_visibility="collapsed")
            if cr_files and st.button("📋 Carica CR", type="primary"):
                for cr in cr_files:
                    with st.spinner(f"{cr.name}..."):
                        r = api("post", "/change-request", files={"file": (cr.name, cr.getvalue(), cr.type)})
                    if r: st.success(r.json().get("message","CR caricata."))

    with tab3:
        section("⚠️ Zone pericolose")
        col_a, col_b = st.columns(2, gap="large")

        with col_a:
            st.markdown("""<div class="card" style="border-color:#FEE2E2;">
                <div style="font-size:1.5rem;margin-bottom:8px;">🗑</div>
                <div style="font-weight:700;color:#1E0A3C;margin-bottom:4px;">Documenti di progetto</div>
                <div style="color:#6B7280;font-size:.83rem;margin-bottom:16px;">
                    Elimina chunk e riassunti di tutti i documenti caricati.<br>
                    <strong>Decisioni e alert rimangono intatti.</strong>
                </div>
            </div>""", unsafe_allow_html=True)
            if "cd" not in st.session_state: st.session_state.cd = False
            if not st.session_state.cd:
                if st.button("Cancella documenti", type="secondary", key="b_cd"):
                    st.session_state.cd = True; st.rerun()
            else:
                st.error("Sei sicuro? Operazione irreversibile.")
                c1,c2 = st.columns(2)
                if c1.button("Sì, cancella", type="primary", key="cd_y"):
                    r = api("delete", "/memory")
                    st.session_state.cd = False
                    if r:
                        d = r.json().get("deleted",{})
                        st.success(f"Rimossi {d.get('IPMP_DOCS',0)} chunk, {d.get('IPMP_CONTEXT',0)} riassunti")
                if c2.button("Annulla", key="cd_n"):
                    st.session_state.cd = False; st.rerun()

        with col_b:
            st.markdown("""<div class="card" style="border-color:#FEE2E2;">
                <div style="font-size:1.5rem;margin-bottom:8px;">📜</div>
                <div style="font-weight:700;color:#1E0A3C;margin-bottom:4px;">Contratto</div>
                <div style="color:#6B7280;font-size:.83rem;margin-bottom:16px;">
                    Rimuove il contratto dal baseline.<br>
                    <strong>Lo Scope Guardian si disattiverà.</strong>
                </div>
            </div>""", unsafe_allow_html=True)
            if "cc" not in st.session_state: st.session_state.cc = False
            if not st.session_state.cc:
                if st.button("Cancella contratto", type="secondary", key="b_cc"):
                    st.session_state.cc = True; st.rerun()
            else:
                st.error("Sei sicuro? Operazione irreversibile.")
                c1,c2 = st.columns(2)
                if c1.button("Sì, cancella", type="primary", key="cc_y"):
                    r = api("delete", "/contract")
                    st.session_state.cc = False
                    if r: st.success(f"Contratto rimosso ({r.json().get('deleted_chunks',0)} chunk)")
                if c2.button("Annulla", key="cc_n"):
                    st.session_state.cc = False; st.rerun()


# ── SCOPE & CR ────────────────────────────────────────────────────────────────
elif page == "scope":
    hero("🛡 Compliance", "Scope Guardian", "Verifica la conformità contrattuale e gestisci gli alert di Change Request")

    tab1, tab2 = st.tabs(["  🔍  Analisi on-demand  ", "  📋  Storico Alert  "])

    with tab1:
        section("Incolla una richiesta o comunicazione da analizzare")
        comunicazione = st.text_area("", height=160,
                                     placeholder="Es: Il cliente richiede l'integrazione con il sistema CRM legacy e la migrazione dei dati storici...",
                                     label_visibility="collapsed")
        if st.button("🔍  Analizza conformità", type="primary") and comunicazione:
            with st.spinner("Analisi contrattuale in corso..."):
                r = api("post", "/scope-check", json={"comunicazione": comunicazione})
            if r:
                result = r.json()
                if result.get("warning"):
                    st.markdown(f'<div class="card" style="border-color:#FEF3C7;"><span class="badge badge-warn">⚠ {result["warning"]}</span></div>', unsafe_allow_html=True)
                elif result.get("fuori_scope"):
                    conf = result.get("confidenza","")
                    st.markdown(f"""<div class="card" style="border-color:#FECACA;background:#FEF2F2;">
                        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
                            <div style="font-size:2rem;">⚠️</div>
                            <div>
                                <div style="font-weight:800;color:#DC2626;font-size:1.1rem;">FUORI SCOPE</div>
                                <div style="color:#6B7280;font-size:.82rem;">Confidenza: {conf}</div>
                            </div>
                        </div>
                    </div>""", unsafe_allow_html=True)
                    section("Elementi fuori perimetro")
                    for el in result.get("elementi", []):
                        st.markdown(f"- {el}")
                    section("Motivazione")
                    st.markdown(result.get("motivazione",""))
                    clausole = result.get("clausole_rilevanti", [])
                    if clausole:
                        section("Clausole di riferimento")
                        for c in clausole:
                            st.markdown(f'<span class="badge badge-info">📌 {c}</span>', unsafe_allow_html=True)
                else:
                    conf = result.get("confidenza","")
                    st.markdown(f"""<div class="card" style="border-color:#A7F3D0;background:#ECFDF5;">
                        <div style="display:flex;align-items:center;gap:12px;">
                            <div style="font-size:2rem;">✅</div>
                            <div>
                                <div style="font-weight:800;color:#059669;font-size:1.1rem;">IN SCOPE</div>
                                <div style="color:#6B7280;font-size:.82rem;">Confidenza: {conf}</div>
                            </div>
                        </div>
                        <div style="margin-top:12px;color:#374151;font-size:.9rem;">{result.get('motivazione','')}</div>
                    </div>""", unsafe_allow_html=True)

    with tab2:
        col_toggle, _ = st.columns([1,3])
        with col_toggle:
            show_all = st.toggle("Mostra tutti")
        r = api("get", f"/alerts?unhandled_only={not show_all}")
        if r:
            alerts = r.json()
            if not alerts:
                st.markdown('<div class="card" style="text-align:center;padding:32px;"><div style="font-size:1.8rem;">✅</div><div style="font-weight:600;color:#059669;margin-top:8px;">Nessun alert aperto</div></div>', unsafe_allow_html=True)
            for alert in alerts:
                cls = "handled" if alert["is_handled"] else ""
                with st.expander(f"{'✅' if alert['is_handled'] else '⚠️'}  [{alert['created_at'][:10]}]  {alert['source'][:45]}"):
                    st.markdown(alert["alert_text"])
                    if alert.get("cr_draft"):
                        section("Bozza Change Request")
                        st.code(alert["cr_draft"], language=None)
                    if not alert["is_handled"]:
                        if st.button("✓ Segna come gestito", key=f"h_{alert['id']}", type="primary"):
                            api("post", f"/alerts/{alert['id']}/handle")
                            st.rerun()


# ── PLAYBOOK ──────────────────────────────────────────────────────────────────
elif page == "playbook":
    hero("📘 Knowledge", "Project Playbook", "Genera il documento di conoscenza anonimizzato per la Practice Knowledge Base")

    col1, col2 = st.columns([3, 2], gap="large")
    with col1:
        section("Come funziona")
        st.markdown("""<div class="card">
        <div class="steps-list">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-body">
                    <div class="step-title">Lettura riassunti</div>
                    <div class="step-desc">Legge tutti i summary da <code>IPMP_CONTEXT</code> — visione documento-livello</div>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-body">
                    <div class="step-title">Recupero decisioni chiave</div>
                    <div class="step-desc">Estrae le decisioni più rilevanti da <code>IPMP_DECISIONS</code></div>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-body">
                    <div class="step-title">Sintesi anonimizzata</div>
                    <div class="step-desc">Claude sintetizza in 7 sezioni strutturate, rimuovendo dati sensibili</div>
                </div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-body">
                    <div class="step-title">Esportazione .docx</div>
                    <div class="step-desc">File pronto per la Practice Knowledge Base, scaricabile immediatamente</div>
                </div>
            </div>
        </div>
        </div>""", unsafe_allow_html=True)

    with col2:
        st.markdown("""<div class="card-dark" style="text-align:center;padding:40px 24px;">
            <div style="font-size:3rem;margin-bottom:16px;">📘</div>
            <div style="font-weight:800;color:white;font-size:1.1rem;margin-bottom:8px;">Genera il Playbook</div>
            <div style="color:#A78BFA;font-size:.85rem;margin-bottom:24px;">
                Operazione che legge tutta la memoria del progetto.<br>Richiede 1-2 minuti.
            </div>
        </div>""", unsafe_allow_html=True)
        if st.button("🚀  Genera Playbook", type="primary", use_container_width=True):
            with st.spinner("Generazione in corso..."):
                r = api("post", "/playbook")
            if r:
                st.success("Playbook generato!")
                st.download_button(
                    "📥  Scarica playbook.docx", data=r.content,
                    file_name="playbook.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )

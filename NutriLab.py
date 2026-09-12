import streamlit as st
from streamlit_gsheets import GSheetsConnection
import requests
import pandas as pd
import re
import uuid
from fpdf import FPDF
import io
import datetime
import calendar
import plotly.express as px
import json
import os

st.set_page_config(page_title="NutriLab", page_icon="🧪", layout="wide")

# =========================================================
# 🔐 1. SISTEMA DI LOGIN CON ANTI-SCOLLEGAMENTO
# =========================================================
UTENTI = {
    "vins": {"password": "admin!", "nome": "Vincenzo", "is_admin": True},
    "monella": {"password": "user1!", "nome": "Silvia", "is_admin": False},
    "ospite": {"password": "test!", "nome": "Utente Ospite", "is_admin": False}
}

ADMIN_ID = "vins" 

if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "username" not in st.session_state: st.session_state.username = ""
if "is_admin" not in st.session_state: st.session_state.is_admin = False

if not st.session_state.logged_in and "user" in st.query_params:
    q_user = st.query_params["user"]
    if q_user in UTENTI:
        st.session_state.logged_in = True
        st.session_state.username = q_user
        st.session_state.is_admin = UTENTI[q_user]["is_admin"]

if not st.session_state.logged_in:
    st.markdown("<h1 style='text-align: center;'>🔐 Accesso a NutriLab</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login_form"):
            username_input = st.text_input("Username").lower().strip()
            password_input = st.text_input("Password", type="password")
            submit_button = st.form_submit_button("Accedi", use_container_width=True)
            
            if submit_button:
                if username_input in UTENTI and UTENTI[username_input]["password"] == password_input:
                    st.session_state.logged_in = True
                    st.session_state.username = username_input
                    st.session_state.is_admin = UTENTI[username_input]["is_admin"]
                    st.query_params["user"] = username_input 
                    st.success("✅ Accesso effettuato!")
                    st.rerun()
                else:
                    st.error("❌ Username o password errati.")
    st.stop()

# =========================================================
# VARIABILI UTENTE E FOGLIO GOOGLE + OTTIMIZZAZIONE CACHE
# =========================================================
USER_ID = st.session_state.username
IS_ADMIN = st.session_state.is_admin
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1xfr_VrhX8Fciz4_o90dTmaviX6wPY3OHlu3AKD58KEE/edit?usp=drive_link"

conn = st.connection("gsheets", type=GSheetsConnection)

FALLBACK_DB = {
    "Farina di avena": (370.0, 13.5, 68.0, 7.0, 10.0, 1.2, 0.0, 0.0, "g"),
    "Latte (Senza lattosio)": (47.0, 3.4, 5.0, 1.5, 0.0, 1.0, 0.0, 0.0, "ml"),
    "Uova intere": (143.0, 12.5, 0.6, 9.5, 0.0, 3.1, 0.0, 55.0, "pz")
}

@st.cache_data(ttl=600)  # TTL Aumentato a 10 minuti per velocità
def get_user_macros_db(user_id):
    """Funzione ottimizzata che calcola il dizionario Macros solo una volta ogni 10 min o finché non scatta il clear()"""
    try:
        if "INCOLLA_QUI" in SPREADSHEET_URL: return FALLBACK_DB
        df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Macros")
        df = df.dropna(subset=['Nome'])
        
        if 'Var_Cottura' not in df.columns: df['Var_Cottura'] = 0.0
        if 'Peso_Medio_pz' not in df.columns: df['Peso_Medio_pz'] = 0.0
        if 'Unita_Default' not in df.columns: df['Unita_Default'] = 'g'
        if 'User_ID' not in df.columns: df['User_ID'] = ADMIN_ID 
        
        df_visibile = df[(df['User_ID'] == ADMIN_ID) | (df['User_ID'] == user_id)]
        df_visibile = df_visibile.sort_values('User_ID').drop_duplicates(subset=['Nome'], keep='last')
        
        mdb = {}
        for _, row in df_visibile.iterrows():
            nome = str(row['Nome']).strip()
            cal = float(row.get('Calorie', 0.0)) if pd.notna(row.get('Calorie')) else 0.0
            c = float(row.get('Carboidrati', 0.0)) if pd.notna(row.get('Carboidrati')) else 0.0
            p = float(row.get('Proteine', 0.0)) if pd.notna(row.get('Proteine')) else 0.0
            f = float(row.get('Grassi', 0.0)) if pd.notna(row.get('Grassi')) else 0.0
            sat = float(row.get('di cui saturi', 0.0)) if pd.notna(row.get('di cui saturi')) else 0.0
            fib = float(row.get('Fibre', 0.0)) if pd.notna(row.get('Fibre')) else 0.0
            var_cott = float(row.get('Var_Cottura', 0.0)) if pd.notna(row.get('Var_Cottura')) else 0.0
            peso_pz = float(row.get('Peso_Medio_pz', 0.0)) if pd.notna(row.get('Peso_Medio_pz')) else 0.0
            unita_def = str(row.get('Unita_Default', 'g')).strip().lower()
            if unita_def not in ['g', 'ml', 'pz']: unita_def = 'g'
            
            mdb[nome] = (cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def)
        return mdb if mdb else FALLBACK_DB
    except Exception:
        return FALLBACK_DB

MACROS_DB = get_user_macros_db(USER_ID)

# =========================================================
# FUNZIONI CLOUD E RICERCA
# =========================================================
def salva_su_cloud(nome, cal, p, c, f, sat, fib, var_cott, peso_pz, unita_def):
    try:
        df_current = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Macros", ttl=0)
        df_current = df_current.dropna(subset=['Nome'])
        if 'User_ID' not in df_current.columns: df_current['User_ID'] = ADMIN_ID
            
        mask_esistente = (df_current['Nome'].str.lower() == nome.lower()) & (df_current['User_ID'] == USER_ID)
        df_current = df_current[~mask_esistente]
            
        nuova_riga = pd.DataFrame({
            "Nome": [nome.title()], "Calorie": [cal], "Carboidrati": [c], "Proteine": [p],
            "Grassi": [f], "di cui saturi": [sat], "Fibre": [fib], "Var_Cottura": [var_cott],
            "Peso_Medio_pz": [peso_pz], "Unita_Default": [unita_def], "User_ID": [USER_ID]
        })
        df_updated = pd.concat([df_current, nuova_riga], ignore_index=True)
        
        cols_final = ["Nome", "Calorie", "Carboidrati", "Proteine", "Grassi", "di cui saturi", "Fibre", "Var_Cottura", "Peso_Medio_pz", "Unita_Default", "User_ID"]
        for col in cols_final:
            if col not in df_updated.columns: df_updated[col] = 0.0
        df_updated = df_updated[cols_final]
        
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Macros", data=df_updated)
        st.cache_data.clear()
        return True
    except Exception as e: return False

def elimina_da_cloud(nome):
    try:
        df_current = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Macros", ttl=0)
        if 'User_ID' not in df_current.columns: df_current['User_ID'] = ADMIN_ID
        
        mask_da_eliminare = (df_current['Nome'].str.lower() == nome.lower()) & (df_current['User_ID'] == USER_ID)
        df_current = df_current[~mask_da_eliminare]
        
        conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Macros", data=df_current)
        st.cache_data.clear()
        return True
    except Exception as e: return False

@st.cache_data(ttl=3600) # Cache pesante per le chiamate API esterne
def cerca_alimento_web(nome):
    url = f"https://it.openfoodfacts.org/cgi/search.pl?search_terms={nome}&search_simple=1&action=process&json=1&page_size=5"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("products") and len(res["products"]) > 0:
            for prod in res["products"]:
                n = prod.get("nutriments", {})
                if "energy-kcal_100g" in n or "proteins_100g" in n:
                    cal = float(n.get("energy-kcal_100g", 0.0) or 0.0)
                    p = float(n.get("proteins_100g", 0.0) or 0.0)
                    c = float(n.get("carbohydrates_100g", 0.0) or 0.0)
                    f = float(n.get("fat_100g", 0.0) or 0.0)
                    fib = float(n.get("fiber_100g", 0.0) or 0.0)
                    sat = float(n.get("saturated-fat_100g", 0.0) or 0.0)
                    return True, cal, p, c, f, fib, sat, 0.0, 0.0, "g"
    except: pass
    return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "g"

def cerca_locale(nome):
    nome_clean = nome.lower().replace("d'", "di ").strip()
    match_parziale = None
    
    for db_nome, macros in MACROS_DB.items():
        db_clean = db_nome.lower().replace("d'", "di ")
        
        # 1. Match Esatto (Priorità assoluta: se lo trova, si ferma subito)
        if db_clean == nome_clean:
            return True, db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8]
            
        # 2. Match Parziale (Se non c'è match esatto, si accontenta di somiglianze)
        if not match_parziale:
            if db_clean in nome_clean or nome_clean in db_clean:
                match_parziale = (True, db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8])
            elif len(set(nome_clean.split()).intersection(set(db_clean.split()))) >= 2:
                match_parziale = (True, db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8])
                
    if match_parziale: return match_parziale
    return False, "", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "g"

# =========================================================
# VARIABILI DI SESSIONE E AUTOSAVE IN BACKGROUND
# =========================================================
RUOLI_LIST = ["Impasto", "Farcitura", "Topping", "Salsa", "Decorazione", "Altro"]
CATEGORIE_LIST = ["☕ Colazione", "🍰 Dessert", "🍝 Primo", "🥩 Secondo", "🍲 Piatto unico", "🥪 Spuntino", "💪 Post work-out", "🔹 Altro"]

stati_iniziali = [
    ('nome_ricetta', "Nuova Ricetta"), ('tipo_ricetta', []), ('procedimento', ""), ('ingredients', []), 
    ('ing_scelto', "-- Seleziona --"), ('input_qty', None), ('input_unit', "g"), ('input_pz_w', 0.0), 
    ('input_ruolo', "Impasto"), ('input_cal', None), ('input_p', None), ('input_c', None), ('input_f', None), 
    ('input_fib', None), ('input_sat', None), ('new_name_free', ""), ('new_name_manual', ""),
    ('riposo', ""), ('porzioni', 1), ('richiede_cottura', False), ('m_cot', "Forno"), ('t_cot', ""),
    ('temp_cot', 180), ('qta_teglia', 100.0), ('tipo_resa', "Usa % di stima"), ('var_cottura', -15.0), ('peso_cotto_reale', 85.0),
    ('confirm_del', ""), ('confirm_del_diario', None), ('temp_recipe_diario', []), ('diario_multi_items', []),
    ('db_nome', ""), ('db_cal', 0.0), ('db_p', 0.0), ('db_c', 0.0), ('db_f', 0.0), ('db_sat', 0.0), ('db_fib', 0.0), ('db_var_cottura', 0.0), ('db_peso_pz', 0.0), ('db_unita_def', 'g'),
    ('vassoio_ing_scelto', "-- Seleziona --"), ('vassoio_qta', None), ('vassoio_unit', 'g'), ('vassoio_pz_w', 0.0), ('chk_cotto', False), ('var_cottura_computed', 0.0),
    ('ing_lib_sel', "-- Seleziona --"), ('qta_lib_val', None), ('unit_lib_val', 'g'), ('lib_pz_w', 0.0), ('confirm_del_prod', None)
]

for key, default in stati_iniziali:
    if key not in st.session_state: st.session_state[key] = default

def salva_bozza_locale():
    if not st.session_state.get('username'): return
    file_name = f"bozza_{st.session_state.username}.json"
    try:
        dati = {"nome_ricetta": st.session_state.get("nome_ricetta", "Nuova Ricetta"), "ingredients": st.session_state.ingredients}
        with open(file_name, "w") as f: json.dump(dati, f)
    except: pass

def carica_bozza_locale():
    if not st.session_state.get('username'): return
    file_name = f"bozza_{st.session_state.username}.json"
    if os.path.exists(file_name):
        try:
            with open(file_name, "r") as f:
                dati = json.load(f)
                st.session_state.nome_ricetta = dati.get("nome_ricetta", "Nuova Ricetta")
                st.session_state.ingredients = dati.get("ingredients", [])
        except: pass

if "bozza_recuperata" not in st.session_state:
    if st.session_state.logged_in: carica_bozza_locale()
    st.session_state.bozza_recuperata = True

def svuota_laboratorio():
    st.session_state.ingredients = []
    st.session_state.nome_ricetta = "Nuova Ricetta"
    salva_bozza_locale()

# =========================================================
# FUNZIONI PARSING E CALCOLO
# =========================================================
def get_macros_and_match(nome):
    nome_clean = nome.lower().replace("d'", "di ").strip()
    for db_nome, macros in MACROS_DB.items():
        if db_nome.lower() == nome_clean: return db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8]
    for db_nome, macros in MACROS_DB.items():
        db_clean = db_nome.lower().replace("d'", "di ")
        if db_clean in nome_clean or nome_clean in db_clean: return db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8]
        if len(set(nome_clean.split()).intersection(set(db_clean.split()))) >= 2: return db_nome, macros[0], macros[1], macros[2], macros[3], macros[4], macros[5], macros[6], macros[7], macros[8]
    
    trovato, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def = cerca_alimento_web(nome)
    if trovato: return None, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def
    return None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "g"

def parse_ingredient_line(line):
    line = line.strip()
    if not line or line.startswith('#'): return None
    line = re.sub(r'^[\-\*\•]\s*', '', line)
    if ',' in line:
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 2:
            try:
                qty = float(parts[1])
                unit_str = parts[2].lower() if len(parts) > 2 else ""
                unit = 'ml' if unit_str in ['ml', 'l'] else 'pz' if unit_str in ['pz', 'pezzi'] else 'g'
                if unit_str in ['kg', 'l']: qty *= 1000
                return qty, unit, parts[0]
            except ValueError: pass 
    match = re.match(r'^([0-9\.,]+)\s*(g|gr|ml|l|pz|kg|cucchiai|cucchiaini)?\s*(?:di\s+|d\')?\s*(.*)$', line, re.IGNORECASE)
    if match:
        qty = float(match.group(1).replace(',', '.'))
        u_s = (match.group(2) or '').lower()
        u = 'ml' if u_s in ['ml','l'] else 'pz' if u_s == 'pz' else 'g'
        if u_s in ['kg', 'l']: qty *= 1000
        return qty, u, match.group(3).strip()
    return 100.0, 'g', line 

def process_ingredient_list(lines):
    aggiunti = 0
    for line in lines:
        parsed = parse_ingredient_line(line)
        if not parsed: continue
        qty, unit, name = parsed
        m_name, cal, p, c, f, fib, sat, var_cott, db_peso_pz, db_unita = get_macros_and_match(name)
        if unit == 'g' and qty < 20 and any(x in name.lower() for x in ["uov", "banan", "datter"]): 
            unit = 'pz'
        
        st.session_state.ingredients.append({
            "id": uuid.uuid4().hex, "nome": name.title(), "matched_name": m_name, 
            "quantita": qty, "unita": unit, "peso_pz": db_peso_pz, "peso": qty * db_peso_pz if unit == 'pz' else qty, 
            "ruolo": "Impasto",
            "cal_100": cal, "prot_100": p, "carb_100": c, "fat_100": f, "sat_100": sat, "fib_100": fib
        })
        aggiunti += 1
    salva_bozza_locale()
    return aggiunti

def ricalcola_ingrediente(ing_id):
    for ing in st.session_state.ingredients:
        if ing['id'] == ing_id:
            ing['nome'] = st.session_state.get(f"n_{ing_id}", ing['nome'])
            ing['quantita'] = st.session_state.get(f"q_{ing_id}", ing['quantita'])
            ing['unita'] = st.session_state.get(f"u_{ing_id}", ing['unita'])
            ing['ruolo'] = st.session_state.get(f"ruolo_{ing_id}", ing.get('ruolo', 'Impasto'))
            if ing['unita'] == 'pz': ing['peso_pz'] = st.session_state.get(f"pw_{ing_id}", 0.0)
            ing['peso'] = ing['quantita'] * ing['peso_pz'] if ing['unita'] == 'pz' else ing['quantita']
            ing['cal_100'] = st.session_state.get(f"cal2_{ing_id}", ing['cal_100'])
            ing['prot_100'] = st.session_state.get(f"p2_{ing_id}", ing['prot_100'])
            ing['carb_100'] = st.session_state.get(f"c2_{ing_id}", ing['carb_100'])
            ing['fat_100'] = st.session_state.get(f"f2_{ing_id}", ing['fat_100'])
            ing['sat_100'] = st.session_state.get(f"sat2_{ing_id}", ing['sat_100'])
            ing['fib_100'] = st.session_state.get(f"fib2_{ing_id}", ing['fib_100'])
            break
    salva_bozza_locale()

def safe_fl(val, default=0.0):
    try: return float(val) if pd.notna(val) else default
    except: return default

def ripristina_ricetta(df):
    row0 = df.iloc[0]
    st.session_state.nome_ricetta = str(row0.get('Ricetta_Nome', 'Nuova Ricetta'))
    st.session_state.procedimento = str(row0.get('Ricetta_Procedimento', ''))
    st.session_state.riposo = str(row0.get('Ricetta_Riposo', ''))
    
    cat_str = str(row0.get('Ricetta_Categorie', ''))
    if cat_str and cat_str != 'nan':
        st.session_state.tipo_ricetta = [c.strip() for c in cat_str.split(',')]
    
    st.session_state.porzioni = int(safe_fl(row0.get('Ricetta_Porzioni', 1)))
    st.session_state.richiede_cottura = bool(row0.get('Cottura_Richiesta', False))
    st.session_state.m_cot = str(row0.get('Cottura_Modalita', 'Forno'))
    st.session_state.t_cot = str(row0.get('Cottura_Tempo', ''))
    st.session_state.temp_cot = int(safe_fl(row0.get('Cottura_Temperatura', 180)))
    st.session_state.tipo_resa = str(row0.get('Cottura_TipoResa', 'Usa % di stima'))
    
    if 'Cottura_Variazione' in row0: st.session_state.var_cottura = float(safe_fl(row0['Cottura_Variazione'], -15.0))
    else: st.session_state.var_cottura = -float(safe_fl(row0.get('Cottura_Calo', 15.0)))
        
    st.session_state.peso_cotto_reale = float(safe_fl(row0.get('Cottura_PesoReale', 85.0)))
    st.session_state.qta_teglia = float(safe_fl(row0.get('Cottura_QtaTeglia', 100.0)))

    st.session_state.ingredients = []
    for _, row in df.iterrows():
        qty = safe_fl(row['Quantita'])
        u = str(row['Unita']).strip()
        pz_w = safe_fl(row.get('Peso_pz'), 0.0)
        st.session_state.ingredients.append({
            "id": uuid.uuid4().hex, "nome": str(row['Nome']).strip(), "matched_name": str(row['Nome']).strip(),
            "quantita": qty, "unita": u, "peso_pz": pz_w, "peso": qty * pz_w if u == 'pz' else qty,
            "ruolo": str(row['Utilizzo']) if pd.notna(row['Utilizzo']) else 'Impasto',
            "cal_100": safe_fl(row.get('Cal_100g'), 0.0), "prot_100": safe_fl(row.get('Prot_100g'), 0.0),
            "carb_100": safe_fl(row.get('Carb_100g'), 0.0), "fat_100": safe_fl(row.get('Fat_100g'), 0.0),
            "sat_100": safe_fl(row.get('Sat_100g'), 0.0), "fib_100": safe_fl(row.get('Fib_100g'), 0.0)
        })
    salva_bozza_locale()

# ==========================================
# 🧭 BARRA LATERALE E NAVIGAZIONE
# ==========================================
st.sidebar.title("🧭 Navigazione")
st.sidebar.markdown(f"👤 Ciao, **{UTENTI[USER_ID]['nome']}**")
if st.sidebar.button("🚪 Logout", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.is_admin = False
    if "user" in st.query_params: del st.query_params["user"]
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()
pagina_corrente = st.sidebar.radio("Scegli l'area di lavoro:", ["🧪 Laboratorio Ricette", "📅 Diario Alimentare", "🗄️ Database Prodotti", "👤 Profilo e Obiettivi"])
st.sidebar.divider()
st.sidebar.markdown("<div style='text-align: center; color: gray;'><small>⚡ Powerd by iannovins</small></div>", unsafe_allow_html=True)

# ==========================================
# 🧪 PAGINA 1: LABORATORIO RICETTE
# ==========================================
if pagina_corrente == "🧪 Laboratorio Ricette":
    
    st.title("🧪 NutriLab")
    st.markdown("#### *Progetta, bilancia e cucina le tue idee.* 💡 ⚖️ 🍳")
    st.write("")

    nome_ric_display = st.session_state.get('nome_ricetta', '').strip()
    if nome_ric_display and nome_ric_display != "Nuova Ricetta":
        st.markdown(f"<h2 style='color: #FF4B4B;'>{nome_ric_display}</h2>", unsafe_allow_html=True)
        st.write("")

    col_titolo, col_svuota, col_ricarica = st.columns([3, 1, 1])
    with col_titolo:
        st.markdown("### :green[1. Aggiungi Ingredienti]")
    with col_svuota:
        st.markdown("<div style='margin-top: 0px;'></div>", unsafe_allow_html=True)
        st.button("🧹 Svuota Laboratorio", on_click=svuota_laboratorio, use_container_width=True)
    with col_ricarica:
        st.markdown("<div style='margin-top: 0px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Ricarica Database", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    tab_manuale, tab_cloud, tab_excel, tab_web, tab_testo = st.tabs(["✍️ Singolo", "☁️ Da Cloud", "📁 Da Excel/CSV", "🌐 Link Web", "📝 Testo"])

    with tab_manuale:
        st.write("Inserisci manualmente o cerca nel database web.")
        opzioni = ["-- Seleziona --", "Altro (Ricerca Libera su Web)", "Altro (Inserimento Manuale)"] + sorted(list(MACROS_DB.keys()))
        
        def update_macros_from_selection():
            scelta = st.session_state.get("ing_scelto", "-- Seleziona --")
            if scelta in ["-- Seleziona --", "Altro (Inserimento Manuale)", "Altro (Ricerca Libera su Web)"]:
                st.session_state.input_cal = None; st.session_state.input_p = None; st.session_state.input_c = None
                st.session_state.input_f = None; st.session_state.input_sat = None; st.session_state.input_fib = None
                st.session_state.input_unit = 'g'
                st.session_state.input_pz_w = 0.0
            else:
                m_name, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def = get_macros_and_match(scelta)
                st.session_state.input_cal = float(cal); st.session_state.input_p = float(p); st.session_state.input_c = float(c)
                st.session_state.input_f = float(f); st.session_state.input_sat = float(sat); st.session_state.input_fib = float(fib)
                st.session_state.input_unit = unita_def
                st.session_state.input_pz_w = peso_pz

        def fetch_macros_from_web_btn():
            new_name = st.session_state.get("new_name_free", "")
            if new_name:
                m_name, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def = get_macros_and_match(new_name)
                st.session_state.input_cal = float(cal); st.session_state.input_p = float(p); st.session_state.input_c = float(c)
                st.session_state.input_f = float(f); st.session_state.input_sat = float(sat); st.session_state.input_fib = float(fib)

        st.selectbox("Cerca ingrediente", options=opzioni, key="ing_scelto", on_change=update_macros_from_selection)
        if st.session_state.get("ing_scelto") == "Altro (Ricerca Libera su Web)":
            c_t, c_b = st.columns([3, 1])
            c_t.text_input("Nome da cercare online:", key="new_name_free")
            c_b.write(""); c_b.button("🔍 Cerca Online", on_click=fetch_macros_from_web_btn)
        elif st.session_state.get("ing_scelto") == "Altro (Inserimento Manuale)":
            st.text_input("Nome nuovo ingrediente:", key="new_name_manual")

        c_q, c_u, c_r, c_pw = st.columns([1, 1, 1, 1.5])
        qty = c_q.number_input("Quantità", min_value=0.0, step=1.0, key="input_qty", value=None)
        unit = c_u.selectbox("Unità", options=["g", "ml", "pz"], key="input_unit")
        ruolo = c_r.selectbox("Utilizzo", options=RUOLI_LIST, key="input_ruolo")
        
        if unit == "pz":
            default_pz_lab = 0.0
            scelta = st.session_state.get("ing_scelto", "-- Seleziona --")
            if scelta not in ["-- Seleziona --", "Altro (Inserimento Manuale)", "Altro (Ricerca Libera su Web)"] and scelta in MACROS_DB:
                default_pz_lab = MACROS_DB[scelta][7]
            pz_w = c_pw.number_input(f"Peso 1 pz (g) [da DB]", min_value=0.0, step=1.0, value=float(default_pz_lab), key="input_pz_w")
        else:
            pz_w = 0.0

        c_cal, c_c, c_p, c_f, c_s, c_fib = st.columns(6)
        val_cal = c_cal.number_input("Calorie", key="input_cal", step=1.0, value=st.session_state.get("input_cal", None))
        val_c = c_c.number_input("Carboidrati", key="input_c", step=0.1, value=st.session_state.get("input_c", None))
        val_p = c_p.number_input("Proteine", key="input_p", step=0.1, value=st.session_state.get("input_p", None))
        val_f = c_f.number_input("Grassi", key="input_f", step=0.1, value=st.session_state.get("input_f", None))
        val_sat = c_s.number_input("di cui saturi", key="input_sat", step=0.1, value=st.session_state.get("input_sat", None))
        val_fib = c_fib.number_input("Fibre", key="input_fib", step=0.1, value=st.session_state.get("input_fib", None))

        def aggiungi_singolo():
            scelta = st.session_state.get("ing_scelto", "-- Seleziona --")
            act = st.session_state.get("new_name_free", "") if scelta == "Altro (Ricerca Libera su Web)" else (st.session_state.get("new_name_manual", "") if scelta == "Altro (Inserimento Manuale)" else scelta)
            if qty is not None and qty > 0 and act and act != "-- Seleziona --":
                m_name, m_cal, m_p, m_c, m_f, m_fib, m_sat, m_var, m_pesopz, m_unita = get_macros_and_match(act)
                st.session_state.ingredients.append({
                    "id": uuid.uuid4().hex, "nome": act.title(), "matched_name": m_name, "quantita": float(qty), "unita": unit, 
                    "peso_pz": float(pz_w), "peso": float(qty) * pz_w if unit == 'pz' else float(qty), 
                    "ruolo": st.session_state.get("input_ruolo", "Impasto"),
                    "cal_100": float(st.session_state.get("input_cal") or 0.0),
                    "prot_100": float(st.session_state.get("input_p") or 0.0), 
                    "carb_100": float(st.session_state.get("input_c") or 0.0), 
                    "fat_100": float(st.session_state.get("input_f") or 0.0), 
                    "sat_100": float(st.session_state.get("input_sat") or 0.0),
                    "fib_100": float(st.session_state.get("input_fib") or 0.0)
                })
                st.session_state.input_qty = None; st.session_state.ing_scelto = "-- Seleziona --"; st.session_state.input_ruolo = "Impasto"
            salva_bozza_locale()

        can_add = True
        if unit == "pz" and pz_w <= 0:
            st.warning("⚠️ Hai selezionato 'pz' ma il peso medio è 0. Inserisci il peso per pezzo per poter aggiungere l'ingrediente.")
            can_add = False

        st.button("➕ Aggiungi", type="primary", on_click=aggiungi_singolo, disabled=(qty is None or qty <= 0 or not can_add))

    with tab_cloud:
        st.write("Gestisci le tue ricette o esplora quelle della community.")
        try:
            df_ricette = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", ttl=600)
            df_ricette = df_ricette.dropna(subset=['Nome Ricetta'])
            
            if 'User_ID' not in df_ricette.columns: df_ricette['User_ID'] = ADMIN_ID
            if 'Condivisa' not in df_ricette.columns: df_ricette['Condivisa'] = True

            sub_personale, sub_community = st.tabs(["📕 Il mio Ricettario", "🌍 Ricette Community"])
            
            with sub_personale:
                df_mie = df_ricette[df_ricette['User_ID'] == USER_ID]
                
                if not df_mie.empty:
                    ricette_list = [f"{row['Nome Ricetta']} [{row['Categoria']}]" for _, row in df_mie.iterrows()]
                    ric_scelta = st.selectbox("Le tue ricette:", ["-- Seleziona --"] + ricette_list, key="sel_mie")
                    
                    c_btn_imp, c_btn_del = st.columns(2)
                    
                    if c_btn_imp.button("📥 Importa nel Laboratorio", use_container_width=True) and ric_scelta != "-- Seleziona --":
                        st.session_state.confirm_del = "" 
                        with st.spinner("Caricamento..."):
                            nome_sel = ric_scelta.rsplit(" [", 1)[0]
                            json_dati = df_mie[df_mie['Nome Ricetta'] == nome_sel]['Dati JSON'].iloc[0]
                            df_rec = pd.read_json(io.StringIO(json_dati))
                            ripristina_ricetta(df_rec)
                            st.success("✅ Ricetta caricata nel laboratorio!")
                            st.rerun()
                            
                    if c_btn_del.button("🗑️ Elimina", type="secondary", use_container_width=True) and ric_scelta != "-- Seleziona --":
                        st.session_state.confirm_del = ric_scelta 

                    if st.session_state.get("confirm_del") == ric_scelta and ric_scelta != "-- Seleziona --":
                        st.warning("⚠️ Sei sicuro di voler eliminare questa ricetta?")
                        c_yes, c_no = st.columns(2)
                        if c_yes.button("🚨 Conferma", type="primary"):
                            nome_sel = ric_scelta.rsplit(" [", 1)[0]
                            mask_del = (df_ricette['Nome Ricetta'] == nome_sel) & (df_ricette['User_ID'] == USER_ID)
                            df_rimasta = df_ricette[~mask_del]
                            conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", data=df_rimasta)
                            st.cache_data.clear()
                            st.session_state.confirm_del = ""
                            st.success("Ricetta eliminata.")
                            st.rerun()
                        if c_no.button("❌ Annulla"):
                            st.session_state.confirm_del = ""
                            st.rerun()
                else:
                    st.info("Non hai ancora salvato nessuna ricetta nel tuo ricettario personale.")

            with sub_community:
                df_comm = df_ricette[(df_ricette['Condivisa'] == True) & (df_ricette['User_ID'] != USER_ID)]
                
                if not df_comm.empty:
                    comm_list = [f"{row['Nome Ricetta']} (di {row['User_ID']}) [{row['Categoria']}]" for _, row in df_comm.iterrows()]
                    ric_comm_scelta = st.selectbox("Esplora le ricette degli altri utenti:", ["-- Seleziona --"] + comm_list, key="sel_comm")
                    
                    if ric_comm_scelta != "-- Seleziona --":
                        nome_comm_sel = ric_comm_scelta.rsplit(" (di ", 1)[0]
                        autore = ric_comm_scelta.split("(di ")[1].split(")")[0]
                        
                        st.write(f"Vuoi aggiungere **{nome_comm_sel}** creata da *{autore}* al tuo ricettario?")
                        
                        if st.button("⬇️ Salva nel mio Ricettario", type="primary", use_container_width=True):
                            with st.spinner("Importazione in corso..."):
                                riga_orig = df_comm[(df_comm['Nome Ricetta'] == nome_comm_sel) & (df_comm['User_ID'] == autore)].copy()
                                riga_orig['User_ID'] = USER_ID
                                riga_orig['Condivisa'] = False
                                
                                if not df_ricette[(df_ricette['Nome Ricetta'] == nome_comm_sel) & (df_ricette['User_ID'] == USER_ID)].empty:
                                    riga_orig['Nome Ricetta'] = nome_comm_sel + " (Importata)"
                                
                                df_updated = pd.concat([df_ricette, riga_orig], ignore_index=True)
                                conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", data=df_updated)
                                st.cache_data.clear()
                                st.success("✅ Ricetta importata! Ora la trovi nella scheda 'Il mio Ricettario'.")
                else:
                    st.info("Nessuna ricetta condivisa dalla community al momento.")

        except Exception as e:
            st.error(f"Crea le colonne Nome Ricetta, Categoria, Dati JSON, User_ID, Condivisa. Errore: {e}")

    with tab_excel:
        st.info("Carica un file CSV o Excel esportato da NutriLab")
        file_caricato = st.file_uploader("Scegli file Excel/CSV", type=['xls', 'xlsx', 'csv'])
        if file_caricato and st.button("📥 Importa da File Esterno"):
            try:
                if file_caricato.name.endswith('.csv'): df = pd.read_csv(file_caricato)
                else: df = pd.read_excel(file_caricato)
                if 'Ricetta_Nome' in df.columns:
                    ripristina_ricetta(df)
                    st.success("✅ Ricetta ripristinata dal file con successo!")
                    st.rerun()
                else:
                    st.session_state.nome_ricetta = file_caricato.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ').title()
                    if file_caricato.name.endswith('.csv'): df = pd.read_csv(file_caricato, header=None)
                    else: df = pd.read_excel(file_caricato, header=None)
                    lines = [f"{str(r.iloc[0]).strip()},{float(r.iloc[1])},{str(r.iloc[2]).strip().lower() if len(r)>2 else ''}" for _, r in df.iterrows()]
                    with st.spinner("Importazione in corso..."): aggiunti = process_ingredient_list(lines)
                    st.success(f"{aggiunti} ingredienti generici importati!")
                    st.rerun()
            except Exception as e: st.error(f"Errore nella lettura del file. {e}")

    with tab_web:
        st.info("Incolla il link di un blog (es. GialloZafferano).")
        url_input = st.text_input("Link della ricetta (URL):")
        if st.button("🌐 Importa da Link Web") and url_input:
            try:
                from recipe_scrapers import scrape_me
                scraper = scrape_me(url_input)
                st.session_state.nome_ricetta, st.session_state.procedimento = scraper.title(), scraper.instructions()
                with st.spinner("Scraping e analisi in corso..."): aggiunti = process_ingredient_list(scraper.ingredients())
                st.success(f"Estrazione completata! {aggiunti} ingredienti trovati."); st.rerun()
            except Exception as e: st.error("Libreria mancante (recipe-scrapers) o link non supportato.")

    with tab_testo:
        st.info("Formato testuale richiesto: **Nome Ingrediente, Quantità, Unità(opzionale)**.")
        testo_input = st.text_area("Incolla qui gli ingredienti (uno per riga):", height=150, placeholder="Es:\ndatteri, 100, g\nuova, 2")
        if st.button("📝 Analizza e Importa") and testo_input:
            with st.spinner("Ricerca ed estrazione in corso..."): aggiunti = process_ingredient_list(testo_input.split('\n'))
            st.success(f"{aggiunti} ingredienti interpretati!"); st.rerun()

    st.divider()

    if st.session_state.ingredients:
        st.markdown("### :orange[2. Riepilogo Ingredienti]")
        
        st.markdown(
            """
            <style>
            [data-testid="column"] [data-testid="stCheckbox"] {
                margin-top: 10px;
            }
            </style>
            """, unsafe_allow_html=True
        )
        
        col_h1, col_sel, col_desel, col_del = st.columns([2.5, 1.2, 1.2, 1.5])
        col_h1.write("Modifica i valori o salva i nuovi ingredienti nel Database in Cloud.")
        
        col_sel.button("☑️ Seleziona Tutti", on_click=lambda: [st.session_state.update({f"chk_del_{i['id']}": True}) for i in st.session_state.ingredients], use_container_width=True)
        col_desel.button("🔲 Deseleziona", on_click=lambda: [st.session_state.update({f"chk_del_{i['id']}": False}) for i in st.session_state.ingredients], use_container_width=True)
        if col_del.button("🗑️ Elimina Selezionati", type="primary", use_container_width=True):
            st.session_state.ingredients = [i for i in st.session_state.ingredients if not st.session_state.get(f"chk_del_{i['id']}", False)]
            salva_bozza_locale()
            st.rerun()
        
        for ing in st.session_state.ingredients:
            non_riconosciuto = (ing['cal_100'] == 0 and ing['prot_100'] == 0 and ing['carb_100'] == 0 and ing['fat_100'] == 0)
            fuzzy_matched = bool(not non_riconosciuto and ing.get('matched_name') and ing['nome'].strip().lower() != ing['matched_name'].lower())
            icona = "⚠️ [DA VERIFICARE]" if non_riconosciuto else "💡 [ASSOCIAZIONE]" if fuzzy_matched else "📌"
            
            ruolo_corr = ing.get('ruolo', 'Impasto')
            titolo_expander = f"{icona} {ing['quantita']} {ing['unita']} di {ing['nome'].title()} (Tot: {ing['peso']:.1f}g) • [{ruolo_corr}]"
            
            col_chk, col_exp = st.columns([0.05, 0.95])
            
            with col_chk:
                st.checkbox(" ", key=f"chk_del_{ing['id']}", label_visibility="collapsed")
                
            with col_exp:
                with st.expander(titolo_expander, expanded=bool(non_riconosciuto or fuzzy_matched)):
                    
                    if non_riconosciuto:
                        st.error("Prodotto non riconosciuto.")
                        c_fix, c_btn = st.columns([3, 1])
                        c_fix.selectbox("Sostituisci con prodotto salvato:", ["-- Scegli dal Database --"] + sorted(list(MACROS_DB.keys())), key=f"fix_sel_{ing['id']}")
                        def applica_fix(i_id, k):
                            sc = st.session_state.get(k)
                            if sc and sc != "-- Scegli dal Database --":
                                cal, p, c, f, fib, sat, v, peso_db, unita_db = MACROS_DB[sc]
                                for item in st.session_state.ingredients:
                                    if item['id'] == i_id:
                                        item.update({'nome': sc, 'matched_name': sc, 'cal_100': cal, 'prot_100': p, 'carb_100': c, 'fat_100': f, 'sat_100': sat, 'fib_100': fib})
                                        st.session_state.update({f"n_{i_id}": sc, f"cal2_{i_id}": cal, f"p2_{i_id}": p, f"c2_{i_id}": c, f"f2_{i_id}": f, f"sat2_{i_id}": sat, f"fib2_{i_id}": fib})
                                        
                                        item.update({'unita': unita_db, 'peso_pz': peso_db})
                                        st.session_state[f"u_{i_id}"] = unita_db
                                        
                                        item['peso'] = item['quantita'] * item.get('peso_pz',0.0) if item['unita']=='pz' else item['quantita']
                                        break
                        c_btn.write(""); c_btn.button("🔄 Applica", key=f"btn_fix_{ing['id']}", on_click=applica_fix, args=(ing['id'], f"fix_sel_{ing['id']}"))
                    
                    elif fuzzy_matched:
                        st.info(f"Ho associato **{ing['nome']}** a **{ing['matched_name']}**.")
                        c_f1, c_f2 = st.columns([3, 1]); c_f1.write(f"Vuoi aggiornare il nome e usare quello del database?")
                        def applica_rn(i_id, nm):
                            for it in st.session_state.ingredients:
                                if it['id'] == i_id: it['nome'] = nm; it['matched_name'] = nm; st.session_state[f"n_{i_id}"] = nm; break
                        c_f2.button("✅ Aggiorna Nome", key=f"btn_rn_{ing['id']}", on_click=applica_rn, args=(ing['id'], ing['matched_name']))

                    st.text_input("Nome", value=ing['nome'], key=f"n_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    
                    r1_1, r1_2, r1_r, r1_3 = st.columns([1, 1, 1, 1.5])
                    r1_1.number_input("Quantità", value=float(ing['quantita']), key=f"q_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r1_2.selectbox("Unità", options=["g", "ml", "pz"], index=["g", "ml", "pz"].index(ing['unita']), key=f"u_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    
                    idx_ruolo = RUOLI_LIST.index(ruolo_corr) if ruolo_corr in RUOLI_LIST else 0
                    r1_r.selectbox("Utilizzo", options=RUOLI_LIST, index=idx_ruolo, key=f"ruolo_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))

                    if ing['unita'] == 'pz': 
                        r1_3.number_input("Peso 1 pz (g)", value=float(ing.get('peso_pz', 0.0)), key=f"pw_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    else: 
                        r1_3.write("")
                    
                    r2_cal, r2_c, r2_p, r2_3, r2_4, r2_5, r2_del = st.columns([1, 1, 1, 1, 1, 1, 0.5])
                    r2_cal.number_input("Calorie", value=float(ing['cal_100']), step=1.0, key=f"cal2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r2_c.number_input("Carb.", value=float(ing['carb_100']), step=0.1, key=f"c2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r2_p.number_input("Prot.", value=float(ing['prot_100']), step=0.1, key=f"p2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r2_3.number_input("Grass.", value=float(ing['fat_100']), step=0.1, key=f"f2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r2_4.number_input("di cui saturi", value=float(ing['sat_100']), step=0.1, key=f"sat2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    r2_5.number_input("Fibre", value=float(ing['fib_100']), step=0.1, key=f"fib2_{ing['id']}", on_change=ricalcola_ingrediente, args=(ing['id'],))
                    
                    if ing['nome'].title() not in MACROS_DB:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("☁️ Salva nuovo prodotto nel Database", key=f"db_save_{ing['id']}", type="secondary"):
                            with st.spinner("Sincronizzazione su Google Sheets..."):
                                success = salva_su_cloud(ing['nome'], ing['cal_100'], ing['prot_100'], ing['carb_100'], ing['fat_100'], ing['sat_100'], ing['fib_100'], 0.0, float(ing.get('peso_pz', 0.0)), ing['unita'])
                            if success:
                                st.success(f"✅ {ing['nome'].title()} salvato!")
                                st.rerun()

                    if r2_del.button("🗑️", key=f"del_sn_{ing['id']}"):
                        st.session_state.ingredients = [it for it in st.session_state.ingredients if it['id'] != ing['id']]
                        salva_bozza_locale()
                        st.rerun()

        st.divider()

        ## --- SEZIONE 3: COTTURA E RESA ---
        st.markdown("### :red[3. Cottura e Resa]")
        
        ruoli_stats = {r: {'w':0.0, 'cal':0.0, 'p':0.0, 'c':0.0, 'f':0.0, 's':0.0, 'fib':0.0} for r in RUOLI_LIST}

        for i in st.session_state.ingredients:
            r = i.get('ruolo', 'Impasto')
            if r not in ruoli_stats: r = 'Impasto'
            ruoli_stats[r]['w'] += i['peso']
            ruoli_stats[r]['cal'] += (i['cal_100'] / 100) * i['peso']
            ruoli_stats[r]['p'] += (i['prot_100'] / 100) * i['peso']
            ruoli_stats[r]['c'] += (i['carb_100'] / 100) * i['peso']
            ruoli_stats[r]['f'] += (i['fat_100'] / 100) * i['peso']
            ruoli_stats[r]['s'] += (i['sat_100'] / 100) * i['peso']
            ruoli_stats[r]['fib'] += (i['fib_100'] / 100) * i['peso']

        tot_w = sum(rs['w'] for rs in ruoli_stats.values())
        w_impasto = ruoli_stats['Impasto']['w']
        w_altri = tot_w - w_impasto

        t_cal = sum(rs['cal'] for rs in ruoli_stats.values())
        t_p = sum(rs['p'] for rs in ruoli_stats.values())
        t_c = sum(rs['c'] for rs in ruoli_stats.values())
        t_f = sum(rs['f'] for rs in ruoli_stats.values())
        t_s = sum(rs['s'] for rs in ruoli_stats.values())
        t_fib = sum(rs['fib'] for rs in ruoli_stats.values())

        st.markdown(f"**Peso Totale (a crudo):** {tot_w:.1f} g *(di cui Impasto: {w_impasto:.1f} g, Componenti extra: {w_altri:.1f} g)*")

        with st.expander("🔍 Dettagli Nutrizionali a Crudo", expanded=False):
            c1_r, c2_r = st.columns(2)
            c1_r.markdown(f"**Valori Totali ({tot_w:.1f}g):**\n- Calorie: {t_cal:.0f} kcal\n- Carboidrati: {t_c:.1f}g\n- Proteine: {t_p:.1f}g\n- Grassi: {t_f:.1f}g\n  di cui saturi: {t_s:.1f}g\n- Fibre: {t_fib:.1f}g")
            if tot_w > 0: c2_r.markdown(f"**Valori su 100g di Preparato:**\n- Calorie: {(t_cal/tot_w*100):.0f} kcal\n- Carboidrati: {(t_c/tot_w*100):.1f}g\n- Proteine: {(t_p/tot_w*100):.1f}g\n- Grassi: {(t_f/tot_w*100):.1f}g\n  di cui saturi: {(t_s/tot_w*100):.1f}g\n- Fibre: {(t_fib/tot_w*100):.1f}g")

        richiede_cottura = st.checkbox("🔥 La ricetta prevede una cottura dell'Impasto?", key="richiede_cottura")
        rt = 1.0
        var_cott_finale_per_json = 0.0

        if richiede_cottura:
            st.markdown("#### Impostazioni e Variazione Peso")
            cc1, cc2, cc3 = st.columns(3)
            m_cot = cc1.selectbox("Modalità di cottura", ["Forno", "Padella", "Friggitrice ad aria", "Altro"], key="m_cot")
            t_cot = cc2.text_input("Tempo di cottura (es. 15 min)", key="t_cot")
            temp = cc3.number_input("Temperatura (°C)", min_value=0, step=5, key="temp_cot")
            
            ct1, ct2 = st.columns(2)
            
            if 'qta_teglia' not in st.session_state or st.session_state.qta_teglia == 100.0:
                st.session_state.qta_teglia = float(w_impasto) if w_impasto > 0 else 100.0
                
            p_teg_impasto = ct1.number_input("Quantità IMPASTO a crudo in teglia (g/ml)", min_value=1.0, key="qta_teglia")
            rt = p_teg_impasto / w_impasto if w_impasto > 0 else 1.0

            tipo_resa = ct2.radio("Come vuoi calcolare la resa?", ["Usa % di stima", "Inserisci peso reale"], key="tipo_resa")
            
            if tipo_resa == "Usa % di stima":
                var_cott = st.number_input("% Variazione peso (es. -15 per calo)", step=1.0, key="var_cottura")
                p_cot_impasto = p_teg_impasto * (1 + var_cott / 100.0)
                st.info(f"💡 Il peso cotto dell'IMPASTO sarà di circa: **{p_cot_impasto:.1f} g**")
                var_cott_finale_per_json = var_cott
            else:
                p_cot_impasto = st.number_input("Peso cotto reale dell'IMPASTO (g)", min_value=1.0, key="peso_cotto_reale")
                var_cott = ((p_cot_impasto - p_teg_impasto) / p_teg_impasto) * 100.0 if p_teg_impasto > 0 else 0.0
                st.info(f"💡 La variazione di peso effettiva è stata del: **{var_cott:+.1f}%**")
                var_cott_finale_per_json = var_cott
        else:
            p_tot_uso = st.number_input("Quantità totale a crudo da preparare (g/ml)", min_value=1.0, value=float(tot_w) if tot_w > 0 else 100.0)
            rt = p_tot_uso / tot_w if tot_w > 0 else 1.0
            p_cot_impasto = w_impasto * rt

        w_altri_scalati = w_altri * rt
        peso_finale = p_cot_impasto + w_altri_scalati

        cal_f, p_f, c_f, f_f, s_f, fib_f = t_cal*rt, t_p*rt, t_c*rt, t_f*rt, t_s*rt, t_fib*rt

        if richiede_cottura:
            with st.expander("🔍 Dettagli Nutrizionali a Cotto (Totale Prodotto Finito)", expanded=False):
                c1_c, c2_c = st.columns(2)
                c1_c.markdown(f"**Valori Totali (su {peso_finale:.1f}g complessivi):**\n- Calorie: {cal_f:.0f} kcal\n- Carboidrati: {c_f:.1f}g\n- Proteine: {p_f:.1f}g\n- Grassi: {f_f:.1f}g\n  di cui saturi: {s_f:.1f}g\n- Fibre: {fib_f:.1f}g")
                if peso_finale > 0: c2_c.markdown(f"**Valori su 100g di Prodotto Finito:**\n- Calorie: {(cal_f/peso_finale*100):.0f} kcal\n- Carboidrati: {(c_f/peso_finale*100):.1f}g\n- Proteine: {(p_f/peso_finale*100):.1f}g\n- Grassi: {(f_f/peso_finale*100):.1f}g\n  di cui saturi: {(s_f/peso_finale*100):.1f}g\n- Fibre: {(fib_f/peso_finale*100):.1f}g")

        st.divider()

        ## --- SEZIONE 4: PORZIONI E BILANCIAMENTO ---
        st.markdown("### :violet[4. Porzioni e Composizione]")
        
        n_porz = st.number_input("In quante porzioni finali dividerai la ricetta?", min_value=1, step=1, key="porzioni")
        w_porz = peso_finale / n_porz
        
        tab_res, tab_comp, tab_tgt = st.tabs(["📊 Totale per Porzione", "🧩 Analisi per Componente", "🎯 Bilanciamento Dinamico"])
        
        with tab_res:
            st.markdown("**Valori per SINGOLA PORZIONE (Prodotto Finito)**")
            st.write(f"*(Ogni porzione pesa complessivamente **{w_porz:.1f} g**)*")
            
            cm_cal, cm2, cm1, cm3, cm4, cm5 = st.columns(6)
            cm_cal.markdown(f"**Calorie**\n\n{(cal_f / n_porz):.0f} kcal")
            cm2.markdown(f"**Carb.**\n\n{(c_f / n_porz):.1f} g")
            cm1.markdown(f"**Prot.**\n\n{(p_f / n_porz):.1f} g")
            cm3.markdown(f"**Grassi**\n\n{(f_f / n_porz):.1f} g")
            cm4.markdown(f"**Saturi**\n\n{(s_f / n_porz):.1f} g")
            cm5.markdown(f"**Fibre**\n\n{(fib_f / n_porz):.1f} g")

        with tab_comp:
            st.write("Analisi dell'apporto nutrizionale suddiviso in base al ruolo dell'ingrediente.")
            for r in RUOLI_LIST:
                if ruoli_stats[r]['w'] > 0:
                    if r == 'Impasto': r_w_final = p_cot_impasto / n_porz
                    else: r_w_final = (ruoli_stats[r]['w'] * rt) / n_porz
                    
                    r_cal = (ruoli_stats[r]['cal'] * rt) / n_porz
                    r_p = (ruoli_stats[r]['p'] * rt) / n_porz
                    r_c = (ruoli_stats[r]['c'] * rt) / n_porz
                    r_f = (ruoli_stats[r]['f'] * rt) / n_porz
                    
                    st.markdown(f"**🔹 {r}** (Peso nella porzione: {r_w_final:.1f} g)  \n  Calorie: {r_cal:.0f} kcal | Carboidrati: {r_c:.1f}g | Proteine: {r_p:.1f}g | Grassi: {r_f:.1f}g")

        with tab_tgt:
            st.write("Scegli un target e calcola le quantità esatte necessarie a raggiungerlo.")
            ct1, ct2 = st.columns(2)
            t_mode = ct1.radio("Calcola il target su:", ["Per porzione", "Su 100g di prodotto finito"])
            t_p_req = ct2.number_input("Target g di proteine", min_value=0.0, value=25.0, step=1.0)
            t_tot = t_p_req * n_porz if t_mode == "Per porzione" else (t_p_req * peso_finale) / 100.0
            
            opz_bil = {k: v[1] for k, v in MACROS_DB.items() if len(v) > 1 and v[1] > 0} 
            for i in st.session_state.ingredients:
                if i['prot_100'] > 0: opz_bil[i['nome']] = i['prot_100']
                
            sel_bil = st.multiselect("Con quali ingredienti vuoi raggiungere il target?", options=list(opz_bil.keys()))
            if sel_bil:
                base_p = sum((i['prot_100'] / 100) * i['peso'] for i in st.session_state.ingredients if i['nome'] not in sel_bil)
                if t_tot > base_p:
                    manca = t_tot - base_p
                    st.write(f"Mancano **{manca:.1f}g** di proteine all'intero preparato.")
                    prop = {}
                    if len(sel_bil) > 1:
                        cs = st.columns(len(sel_bil))
                        t_sl = sum(cs[idx].slider(f"% da {s}", 0, 100, int(100/len(sel_bil)), key=f"sl_{idx}") for idx, s in enumerate(sel_bil))
                        if t_sl > 0: prop = {s: st.session_state[f"sl_{idx}"]/t_sl for idx, s in enumerate(sel_bil)}
                    else: prop = {sel_bil[0]: 1.0}
                    
                    if sum(prop.values()) > 0:
                        st.success("📝 Quantità **TOTALI** da inserire a crudo:")
                        for s in sel_bil:
                            if prop.get(s, 0) > 0:
                                st.markdown(f"- **{(((manca * prop[s]) / opz_bil[s]) * 100):.1f} g** di {s}")
                else: st.warning(f"Gli ingredienti coprono già il target!")

        st.divider()

        ## --- SEZIONE 5 E 6: PROCEDIMENTO E ESPORTAZIONE ---
        st.markdown("### :blue[5. Dettagli, Stampa e Salvataggio]")
        
        rip = st.text_input("Tempo di riposo (es. 30 min, in frigo ecc.)", key="riposo")
        proc = st.text_area("Procedimento", height=150, key="procedimento")
        
        c_n, c_t = st.columns([2, 1])
        n_ric = c_n.text_input("**Nome ricetta:**", key="nome_ricetta")
        t_ric = c_t.multiselect("**Categoria:**", CATEGORIE_LIST, key="tipo_ricetta")
        
        txt_exp = f"RICETTA: {n_ric}\nCATEGORIA: {', '.join(t_ric)}\n\nINGREDIENTI:\n"
        ruoli_presenti = set(i.get('ruolo', 'Impasto') for i in st.session_state.ingredients)
        for r_ord in RUOLI_LIST:
            if r_ord in ruoli_presenti:
                txt_exp += f"\n  [{r_ord.upper()}]\n"
                for i in st.session_state.ingredients:
                    if i.get('ruolo', 'Impasto') == r_ord:
                        peso_extra = f" ({i['peso']:.0f}g)" if i['unita'] == 'pz' else ""
                        txt_exp += f"  - {i['quantita']} {i['unita']} {i['nome']}{peso_extra}\n"
        
        txt_exp += f"\nPREPARAZIONE:\n"
        if rip: txt_exp += f"- Riposo: {rip}\n"
        if richiede_cottura: 
            txt_exp += f"- Cottura (solo Impasto): {st.session_state.m_cot} a {st.session_state.temp_cot}°C per {st.session_state.t_cot}\n"
            var_c_txt = st.session_state.var_cottura if st.session_state.tipo_resa == "Usa % di stima" else var_cott
            txt_exp += f"- Variazione peso Impasto: {var_c_txt:+.1f}%\n- Peso Prodotto Finito: {peso_finale:.1f} g\n"
        else: 
            txt_exp += f"- Resa totale: {peso_finale:.1f} g\n"
            
        txt_exp += f"- Porzioni: {n_porz} da {w_porz:.1f} g\n\nVALORI NUTRIZIONALI (Per Porzione Pronta):\n"
        txt_exp += f"- Calorie: {(cal_f/n_porz):.0f} kcal | Carboidrati: {(c_f/n_porz):.1f}g | Proteine: {(p_f/n_porz):.1f}g | Grassi: {(f_f/n_porz):.1f}g (Saturi: {(s_f/n_porz):.1f}g) | Fibre: {(fib_f/n_porz):.1f}g"
        
        def clean(t): return str(t).encode('latin-1', 'replace').decode('latin-1')
        def mk_pdf():
            pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", 'B', 16)
            pdf.multi_cell(0, 10, txt=clean(txt_exp)); return bytes(pdf.output())

        df_export = pd.DataFrame([{
            "Nome": i['nome'], "Quantita": i['quantita'], "Unita": i['unita'], "Utilizzo": i.get('ruolo', 'Impasto'),
            "Cal_100g": i['cal_100'], "Prot_100g": i['prot_100'], "Carb_100g": i['carb_100'],
            "Fat_100g": i['fat_100'], "Sat_100g": i['sat_100'], "Fib_100g": i['fib_100'], "Peso_pz": i.get('peso_pz', 0.0),
            "Ricetta_Nome": st.session_state.nome_ricetta,
            "Ricetta_Procedimento": st.session_state.procedimento,
            "Ricetta_Riposo": st.session_state.riposo,
            "Ricetta_Categorie": ",".join(st.session_state.tipo_ricetta),
            "Ricetta_Porzioni": st.session_state.porzioni,
            "Cottura_Richiesta": st.session_state.richiede_cottura,
            "Cottura_Modalita": st.session_state.m_cot,
            "Cottura_Tempo": st.session_state.t_cot,
            "Cottura_Temperatura": st.session_state.temp_cot,
            "Cottura_TipoResa": st.session_state.tipo_resa,
            "Cottura_Variazione": var_cott_finale_per_json if st.session_state.richiede_cottura else 0.0,
            "Cottura_PesoReale": st.session_state.peso_cotto_reale,
            "Cottura_QtaTeglia": st.session_state.qta_teglia
        } for i in st.session_state.ingredients])
        
        csv_data = df_export.to_csv(index=False).encode('utf-8')
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Ricetta')
        excel_data = excel_buffer.getvalue()

        with st.expander("👀 Anteprima Testo Generato"): st.text(txt_exp)
        
        st.write("**Salvataggio in Cloud**")
        
        if IS_ADMIN:
            condividi_ricetta = True
            st.info("👑 Sei l'amministratore: le tue ricette sono pubbliche di default per tutta la community.")
        else:
            condividi_ricetta = st.checkbox("🌍 Rendi pubblica questa ricetta (condividila con la community)")
            
        if st.button("☁️ Salva Ricetta nel Database Cloud", use_container_width=True):
            if n_ric and st.session_state.ingredients:
                with st.spinner("Salvataggio in corso..."):
                    try:
                        df_ricette = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", ttl=0)
                        if 'User_ID' not in df_ricette.columns: df_ricette['User_ID'] = ADMIN_ID
                        if 'Condivisa' not in df_ricette.columns: df_ricette['Condivisa'] = True

                        # Evita duplicati di nome per lo STESSO utente
                        mask_esistente = (df_ricette['Nome Ricetta'] == n_ric) & (df_ricette['User_ID'] == USER_ID)
                        df_ricette = df_ricette[~mask_esistente]
                        
                        nuova_riga = pd.DataFrame({
                            "Nome Ricetta": [n_ric],
                            "Categoria": [", ".join(t_ric)],
                            "Dati JSON": [df_export.to_json(orient='records')],
                            "User_ID": [USER_ID],
                            "Condivisa": [condividi_ricetta]
                        })
                        
                        df_updated = pd.concat([df_ricette, nuova_riga], ignore_index=True)
                        conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", data=df_updated)
                        st.cache_data.clear()
                        st.success("✅ Ricetta salvata nel Database!")
                    except Exception as e:
                        st.error(f"Errore durante il salvataggio. Assicurati che in Sheets ci siano le colonne 'Nome Ricetta', 'Categoria', 'Dati JSON', 'User_ID', 'Condivisa'. Errore tecnico: {e}")
            else:
                st.warning("⚠️ Inserisci un Nome per la ricetta prima di salvare.")

        st.write("**Esportazione File Locali**")
        c_dl1, c_dl2, c_dl3, c_dl4 = st.columns(4)
        nm_f = n_ric.replace(" ", "_").lower() if n_ric else "ricetta"
        c_dl1.download_button("📄 .TXT", data=txt_exp, file_name=f"{nm_f}.txt", use_container_width=True)
        try: c_dl2.download_button("📕 .PDF", data=mk_pdf(), file_name=f"{nm_f}.pdf", mime="application/pdf", use_container_width=True)
        except: c_dl2.error("Errore PDF")
        c_dl3.download_button("📊 .CSV (Dati)", data=csv_data, file_name=f"{nm_f}.csv", mime="text/csv", use_container_width=True)
        c_dl4.download_button("📗 .XLSX (Dati)", data=excel_data, file_name=f"{nm_f}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

# ==========================================
# 📅 PAGINA 2: DIARIO ALIMENTARE
# ==========================================
elif pagina_corrente == "📅 Diario Alimentare":
    
    st.title("📅 Diario Alimentare")
    st.markdown("#### *Tieni traccia dei tuoi macros giornalieri.* 📊")
    st.write("")

    # --- RECUPERO OBIETTIVI AL TOP DELLA PAGINA (Per salvarli nel diario) ---
    tgt_cal = tgt_c = tgt_p = tgt_f = 0.0
    try:
        df_prof = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Profilo", ttl=600)
        u_prof = df_prof[df_prof['User_ID'] == USER_ID]
        if not u_prof.empty:
            tgt_cal = float(u_prof.iloc[0].get('TGT_Cal', 0) or 0)
            tgt_c = float(u_prof.iloc[0].get('TGT_C', 0) or 0)
            tgt_p = float(u_prof.iloc[0].get('TGT_P', 0) or 0)
            tgt_f = float(u_prof.iloc[0].get('TGT_F', 0) or 0)
    except: pass

    c1, c2 = st.columns(2)
    with c1:
        data_sel = st.date_input("Data di riferimento", pd.to_datetime('today'))
        
        ora_attuale = pd.Timestamp.now(tz='Europe/Rome').time()
        t_colazione = datetime.time(9, 30)
        t_spuntino1 = datetime.time(12, 0)
        t_pranzo = datetime.time(15, 0)
        t_spuntino2 = datetime.time(19, 0)
        
        if ora_attuale <= t_colazione: default_pasto_idx = 0 
        elif ora_attuale <= t_spuntino1: default_pasto_idx = 1 
        elif ora_attuale <= t_pranzo: default_pasto_idx = 2 
        elif ora_attuale <= t_spuntino2: default_pasto_idx = 3 
        else: default_pasto_idx = 4 
        
        pasto_sel = st.selectbox("Pasto della giornata", ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"], index=default_pasto_idx)
    
    with c2:
        tipo_inserimento_diario = st.radio(
            "Seleziona la tipologia di inserimento:", 
            ["📚 Dal tuo Ricettario", "🛒 Alimenti (Singoli o Multipli)", "⏱️ Ricetta Libera (Al volo)"], 
            horizontal=True
        )

    st.divider()
    
    rows_to_add = [] 
    ready_to_add = False
    
    # ---------------------------------------------------------
    # FLUSSO 1: RICETTA DAL RICETTARIO PERSONALE
    # ---------------------------------------------------------
    if tipo_inserimento_diario == "📚 Dal tuo Ricettario":
        try:
            df_ric_cloud = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Ricette", ttl=600)
            if 'User_ID' not in df_ric_cloud.columns: df_ric_cloud['User_ID'] = ADMIN_ID
            df_ric_cloud = df_ric_cloud[df_ric_cloud['User_ID'] == USER_ID]
            ricette_list = df_ric_cloud['Nome Ricetta'].dropna().tolist()
        except:
            ricette_list = []
            df_ric_cloud = pd.DataFrame()
            
        ric_scelta = st.selectbox("Cerca la ricetta nel tuo archivio:", ["-- Seleziona --"] + ricette_list)
        
        if ric_scelta != "-- Seleziona --":
            json_str = df_ric_cloud[df_ric_cloud['Nome Ricetta'] == ric_scelta]['Dati JSON'].iloc[0]
            df_r = pd.read_json(io.StringIO(json_str))
            
            st.markdown("### 1️⃣ La preparazione di oggi")
            with st.expander("🛠️ Modifica ingredienti crudi (solo per questo pasto)", expanded=False):
                mod_qty_raw = {}
                mod_peso_pz = {}
                for idx, row in df_r.iterrows():
                    c1_r, c2_r = st.columns([2, 1]) if row['Unita'] == 'pz' else st.columns([1, 0.01])
                    with c1_r:
                        mod_qty_raw[idx] = st.number_input(f"{row['Nome']} ({row['Unita']}) a crudo", min_value=0.0, value=float(row['Quantita']), step=1.0 if row['Unita'] == 'pz' else 5.0, key=f"mod_raw_{idx}")
                    if row['Unita'] == 'pz':
                        with c2_r: mod_peso_pz[idx] = st.number_input(f"Peso 1 pz (g)", min_value=0.1, value=float(row.get('Peso_pz', 100.0)), step=1.0, key=f"mod_pz_{idx}")
                    else: mod_peso_pz[idx] = 0.0
            
            new_w_impasto_raw = new_w_altri_raw = new_m_cal_tot = new_m_p_tot = new_m_c_tot = new_m_f_tot = new_m_sat_tot = new_m_fib_tot = 0.0
            variante = False
            for idx, row in df_r.iterrows():
                actual_qta = mod_qty_raw[idx]
                if abs(actual_qta - float(row['Quantita'])) > 0.01: variante = True
                if actual_qta > 0:
                    u = str(row['Unita']).strip()
                    pz_w = mod_peso_pz[idx] if u == 'pz' else 0.0
                    w_ing_raw = actual_qta * pz_w if u == 'pz' else actual_qta
                    if str(row.get('Utilizzo', 'Impasto')) == 'Impasto': new_w_impasto_raw += w_ing_raw
                    else: new_w_altri_raw += w_ing_raw
                    
                    new_m_cal_tot += (float(row.get('Cal_100g', 0)) / 100) * w_ing_raw
                    new_m_p_tot += (float(row.get('Prot_100g', 0)) / 100) * w_ing_raw
                    new_m_c_tot += (float(row.get('Carb_100g', 0)) / 100) * w_ing_raw
                    new_m_f_tot += (float(row.get('Fat_100g', 0)) / 100) * w_ing_raw
                    new_m_sat_tot += (float(row.get('Sat_100g', 0)) / 100) * w_ing_raw
                    new_m_fib_tot += (float(row.get('Fib_100g', 0)) / 100) * w_ing_raw
            
            r_cottura = bool(df_r.iloc[0].get('Cottura_Richiesta', False))
            if r_cottura:
                if str(df_r.iloc[0].get('Cottura_TipoResa', '')) == "Usa % di stima":
                    var_cott_db = float(df_r.iloc[0]['Cottura_Variazione']) if 'Cottura_Variazione' in df_r.columns else -float(df_r.iloc[0].get('Cottura_Calo', 15.0))
                    p_cot_new = new_w_impasto_raw * (1 + var_cott_db / 100.0)
                else:
                    vecchio_impasto_raw = float(df_r.iloc[0].get('Cottura_QtaTeglia', 100.0))
                    vecchio_cotto_reale = float(df_r.iloc[0].get('Cottura_PesoReale', 85.0))
                    var_perc = ((vecchio_cotto_reale - vecchio_impasto_raw) / vecchio_impasto_raw) if vecchio_impasto_raw > 0 else -0.15
                    p_cot_new = new_w_impasto_raw * (1 + var_perc)
            else:
                p_cot_new = new_w_impasto_raw
                
            peso_finale_ricetta = p_cot_new + new_w_altri_raw
            peso_crudo_totale = new_w_impasto_raw + new_w_altri_raw
            porz_orig = float(df_r.iloc[0].get('Ricetta_Porzioni', 1.0))
            if porz_orig <= 0: porz_orig = 1.0
            peso_singola_porzione = peso_finale_ricetta / porz_orig
            
            st.info(f"⚖️ **Report Preparazione (Intera):** Peso a crudo: **{peso_crudo_totale:.1f} g** | Peso Cotto/Finito: **{peso_finale_ricetta:.1f} g**")
            
            st.markdown("### 2️⃣ Quanto ne hai mangiato?")
            c_mod1, c_mod2 = st.columns(2)
            tipo_inserimento = c_mod1.radio("Scegli come inserire la quantità consumata:", ["In Porzioni (Frazione)", "Grammi esatti"])
            
            if tipo_inserimento == "In Porzioni (Frazione)":
                qta_val = c_mod2.number_input("Numero di porzioni mangiate", min_value=0.1, step=0.5, value=1.0)
                rt_consumo = qta_val / porz_orig
                peso_consumato = peso_finale_ricetta * rt_consumo
                valore_salvataggio = qta_val; unita_salvataggio = "porzioni"
                st.caption(f"💡 Stai registrando **{peso_consumato:.1f} g** complessivi.")
            else:
                peso_consumato = c_mod2.number_input("Grammi esatti mangiati (g)", min_value=1.0, step=10.0, value=float(peso_singola_porzione))
                rt_consumo = peso_consumato / peso_finale_ricetta if peso_finale_ricetta > 0 else 0
                valore_salvataggio = peso_consumato; unita_salvataggio = "g"
                st.caption(f"💡 Stai registrando **{peso_consumato:.1f} g** complessivi.")
                
            m_cal_disp = new_m_cal_tot * rt_consumo; m_p_disp = new_m_p_tot * rt_consumo; m_c_disp = new_m_c_tot * rt_consumo
            m_f_disp = new_m_f_tot * rt_consumo; m_sat_disp = new_m_sat_tot * rt_consumo; m_fib_disp = new_m_fib_tot * rt_consumo
            elemento_inserito = f"🍽️ {ric_scelta} (Variante)" if variante else f"🍽️ {ric_scelta}"
            
            st.write("")
            st.markdown(f"**Valori Nutrizionali per la quantità consumata ({peso_consumato:.1f} g):**")
            cm_cal, cm2, cm1, cm3, cm4, cm5 = st.columns(6)
            cm_cal.markdown(f"**Calorie**\n\n{m_cal_disp:.0f} kcal"); cm2.markdown(f"**Carb.**\n\n{m_c_disp:.1f} g")
            cm1.markdown(f"**Prot.**\n\n{m_p_disp:.1f} g"); cm3.markdown(f"**Grassi**\n\n{m_f_disp:.1f} g")
            cm4.markdown(f"**Saturi**\n\n{m_sat_disp:.1f} g"); cm5.markdown(f"**Fibre**\n\n{m_fib_disp:.1f} g")

            rows_to_add.append({
                "ID": uuid.uuid4().hex, "Data": str(data_sel), "Pasto": pasto_sel, "Elemento": elemento_inserito,
                "Quantita": valore_salvataggio, "Unita": unita_salvataggio, "Calorie": m_cal_disp, "Carboidrati": m_c_disp, 
                "Proteine": m_p_disp, "Grassi": m_f_disp, "Saturi": m_sat_disp, "Fibre": m_fib_disp, "User_ID": USER_ID,
                "TGT_Cal": tgt_cal, "TGT_C": tgt_c, "TGT_P": tgt_p, "TGT_F": tgt_f
            })
            ready_to_add = True

    # ---------------------------------------------------------
    # FLUSSO 2: ALIMENTI (SINGOLI O MULTIPLI)
    # ---------------------------------------------------------
    elif tipo_inserimento_diario == "🛒 Alimenti (Singoli o Multipli)":
        st.markdown("### 1️⃣ Componi il pasto")
        
        def update_vassoio_from_selection():
            ing = st.session_state.get("vassoio_ing_scelto")
            if ing and ing != "-- Seleziona --":
                m_name, m_cal, m_p, m_c, m_f, m_fib, m_sat, m_var, peso_pz, unita_def = get_macros_and_match(ing)
                st.session_state.vassoio_unit = unita_def
                st.session_state.vassoio_pz_w = peso_pz if peso_pz > 0 else 0.0

        c_ing, c_qta, c_unit, c_pz, c_btn = st.columns([3, 1, 1, 1, 1.5])
        ing_scelto = c_ing.selectbox("Cerca alimento:", ["-- Seleziona --"] + sorted(list(MACROS_DB.keys())), key="vassoio_ing_scelto", on_change=update_vassoio_from_selection)
        qta_val = c_qta.number_input("Quantità (a crudo)", min_value=0.0, step=10.0, key="vassoio_qta", value=None)
        
        idx_u = ["g", "ml", "pz"].index(st.session_state.get("vassoio_unit", "g")) if st.session_state.get("vassoio_unit") in ["g", "ml", "pz"] else 0
        unit_val = c_unit.selectbox("Unità", options=["g", "ml", "pz"], key="vassoio_unit", index=idx_u)
        
        if unit_val == "pz": 
            default_pz = MACROS_DB[ing_scelto][7] if ing_scelto != "-- Seleziona --" and ing_scelto in MACROS_DB else 0.0
            pz_w = c_pz.number_input("Peso 1pz (g) [da DB]", min_value=0.0, step=1.0, value=float(default_pz), key="vassoio_pz_w")
        else: 
            pz_w = 0.0

        if ing_scelto != "-- Seleziona --" and qta_val is not None and qta_val > 0:
            cal_p, p_p, c_p, f_p, _, _, _, _, _ = MACROS_DB[ing_scelto]
            peso_p = qta_val * pz_w if unit_val == "pz" else qta_val
            st.markdown(f"<div style='color:gray; font-size:14px; margin-top:-10px; margin-bottom:10px;'>📊 <b>Valori per {peso_p:.1f}g:</b> {cal_p*peso_p/100:.0f} kcal | C: {c_p*peso_p/100:.1f}g | P: {p_p*peso_p/100:.1f}g | G: {f_p*peso_p/100:.1f}g</div>", unsafe_allow_html=True)

        mostra_cottura = st.checkbox("🔥 Applica calo/aumento peso cottura", key="chk_cotto")
        
        var_cottura_da_salvare = 0.0
        if mostra_cottura and ing_scelto != "-- Seleziona --":
            db_var = MACROS_DB[ing_scelto][6]
            if qta_val is not None and qta_val > 0:
                peso_effettivo_crudo = qta_val * pz_w if unit_val == "pz" else qta_val
                tipo_resa_vassoio = st.radio("Come vuoi calcolare la resa in cottura?", ["Usa % di stima", "Inserisci peso reale cotto"], horizontal=True)
                
                if tipo_resa_vassoio == "Usa % di stima":
                    c_var1, c_var2 = st.columns([1, 2])
                    var_cottura_da_salvare = c_var1.number_input("% Variazione Cottura", value=float(db_var), step=1.0, key=f"var_cott_{ing_scelto}")
                    peso_stimato_cotto = peso_effettivo_crudo * (1 + var_cottura_da_salvare / 100)
                    c_var2.info(f"⚖️ Peso Crudo: **{peso_effettivo_crudo:.1f} g** ➡️ Peso Cotto stimato: **{peso_stimato_cotto:.1f} g**")
                else:
                    c_var1, c_var2 = st.columns([1, 2])
                    peso_stimato_cotto_default = peso_effettivo_crudo * (1 + db_var / 100)
                    peso_cotto_reale = c_var1.number_input("Peso cotto reale (g)", min_value=1.0, value=float(peso_stimato_cotto_default), step=10.0)
                    var_cottura_da_salvare = ((peso_cotto_reale - peso_effettivo_crudo) / peso_effettivo_crudo) * 100 if peso_effettivo_crudo > 0 else 0.0
                    c_var2.info(f"⚖️ Variazione rilevata: **{var_cottura_da_salvare:+.1f}%**")
                    
                    if abs(var_cottura_da_salvare - db_var) > 0.1:
                        if st.button("💾 Aggiorna % nel Database Prodotti", key="btn_upd_var"):
                            with st.spinner("Aggiornamento in corso..."):
                                cal_db, p_db, c_db, f_db, fib_db, sat_db, _, peso_db, unita_db = MACROS_DB[ing_scelto]
                                salva_su_cloud(ing_scelto, cal_db, p_db, c_db, f_db, sat_db, fib_db, var_cottura_da_salvare, peso_db, unita_db)
                                st.success("✅ Variazione di cottura aggiornata!")
                                st.rerun()

        st.session_state.var_cottura_computed = var_cottura_da_salvare
        
        def on_add_multi():
            ing = st.session_state.get("vassoio_ing_scelto", "-- Seleziona --")
            qta = st.session_state.get("vassoio_qta")
            unit = st.session_state.get("vassoio_unit")
            pz_w_val = st.session_state.get("vassoio_pz_w", 0.0)
            cotto = st.session_state.get("chk_cotto", False)
            var_c = float(st.session_state.get("var_cottura_computed", 0.0))
            
            if ing != "-- Seleziona --" and qta is not None and qta > 0 and unit is not None:
                st.session_state.diario_multi_items.append({
                    "id": uuid.uuid4().hex, "nome": ing, "quantita": float(qta), "unita": unit,
                    "peso_pz": float(pz_w_val), "is_cotto": cotto, "var_cottura": var_c
                })
                st.session_state.vassoio_ing_scelto = "-- Seleziona --"
                st.session_state.vassoio_qta = None
                st.session_state.chk_cotto = False

        can_add = True
        if unit_val == "pz" and pz_w <= 0:
            st.warning("⚠️ Hai selezionato 'pz' ma il peso medio è 0.")
            can_add = False

        with c_btn:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            st.button("➕ Aggiungi al Vassoio", use_container_width=True, on_click=on_add_multi, disabled=(not can_add or qta_val is None or qta_val <= 0))

        if st.session_state.diario_multi_items:
            st.markdown("### 🛒 Nel tuo Vassoio:")
            m_cal_tot = m_p_tot = m_c_tot = m_f_tot = m_sat_tot = m_fib_tot = 0.0
            m_peso_tot = 0.0 
            
            ingredienti_list = []
            
            for i, item in enumerate(st.session_state.diario_multi_items):
                c1, c2, c3 = st.columns([0.6, 0.3, 0.1])
                
                new_qty = c2.number_input("Q.tà", min_value=0.0, value=float(item['quantita']), step=1.0 if item['unita'] == 'pz' else 5.0, key=f"edit_multi_{item['id']}", label_visibility="collapsed")
                if new_qty != item['quantita']: st.session_state.diario_multi_items[i]['quantita'] = new_qty
                if c3.button("❌", key=f"del_multi_{item['id']}"):
                    st.session_state.diario_multi_items = [it for it in st.session_state.diario_multi_items if it['id'] != item['id']]
                    st.rerun()

                cal, p, c, f, fib, sat, _, _, _ = MACROS_DB[item["nome"]]
                peso_effettivo = new_qty * item.get("peso_pz", 0.0) if item["unita"] == "pz" else new_qty
                
                cal_i = (cal / 100) * peso_effettivo
                c_i = (c / 100) * peso_effettivo
                p_i = (p / 100) * peso_effettivo
                f_i = (f / 100) * peso_effettivo
                sat_i = (sat / 100) * peso_effettivo
                fib_i = (fib / 100) * peso_effettivo
                
                m_cal_tot += cal_i
                m_p_tot += p_i
                m_c_tot += c_i
                m_f_tot += f_i
                m_sat_tot += sat_i
                m_fib_tot += fib_i
                
                p_cotto_str = ""
                if item.get("is_cotto"):
                    p_cotto = peso_effettivo * (1 + item.get("var_cottura", 0.0)/100)
                    p_cotto_str = f" (Cotto: {p_cotto:.1f} g)"
                    m_peso_tot += p_cotto
                else:
                    m_peso_tot += peso_effettivo
                
                c1.write(f"🔹 **{item['nome']}** {p_cotto_str} *(Cal: {cal_i:.0f} | C: {c_i:.1f}g | P: {p_i:.1f}g | G: {f_i:.1f}g)*")
                ingredienti_list.append(f"{new_qty:g}{item['unita']} {item['nome']}")
                
            st.write("")
            st.info(f"⚖️ **Peso Totale del Vassoio:** {m_peso_tot:.1f} g")
            
            st.markdown("#### 🥧 Resa e Porzioni del Vassoio")
            
            num_porzioni = st.number_input("In quante porzioni totali dividi questo vassoio? (max 5)", min_value=1, max_value=5, step=1, value=1)
            
            porzioni_perc = []
            perc_rimanente = 100.0
            
            if num_porzioni == 1:
                porzioni_perc = [100.0]
                st.info("Il vassoio è considerato come 1 singola porzione (100%).")
            else:
                st.write("Imposta la % per ogni porzione (l'ultima è calcolata in automatico):")
                cols_perc = st.columns(num_porzioni)
                somma_parziale = 0.0
                
                for i in range(num_porzioni - 1):
                    with cols_perc[i]:
                        default_p = 100.0 / num_porzioni
                        p_val = st.number_input(f"% Porz. {i+1}", min_value=0.0, max_value=100.0, value=float(default_p), step=1.0, key=f"perc_p_{i}")
                        porzioni_perc.append(p_val)
                        somma_parziale += p_val
                
                perc_rimanente = 100.0 - somma_parziale
                porzioni_perc.append(perc_rimanente)
                
                with cols_perc[-1]:
                    st.text_input(f"% Porz. {num_porzioni} (Resto)", value=f"{perc_rimanente:.1f}%", disabled=True)
                
                if perc_rimanente < 0:
                    st.error("⚠️ Attenzione: La somma delle percentuali supera il 100%. Riduci i valori.")

            st.divider()
            
            st.markdown("### 2️⃣ Quali porzioni stai mangiando?")
            
            porzioni_selezionate = []
            cols_chk = st.columns(num_porzioni)
            
            for i in range(num_porzioni):
                perc = porzioni_perc[i]
                with cols_chk[i]:
                    if perc >= 0:
                        # Di default spunta solo la prima porzione
                        mangio = st.checkbox(f"🍽️ Mangio Porz. {i+1} ({perc:.1f}%)", value=(i==0), key=f"mangio_chk_{i}")
                        if mangio:
                            porzioni_selezionate.append(i)
                        
                        # Calcolo peso, calorie e MACROS della singola porzione
                        p_peso = m_peso_tot * (perc / 100.0)
                        p_cal = m_cal_tot * (perc / 100.0)
                        p_c = m_c_tot * (perc / 100.0)
                        p_p = m_p_tot * (perc / 100.0)
                        p_f = m_f_tot * (perc / 100.0)
                        
                        st.caption(f"⚖️ {p_peso:.1f}g | 🔥 {p_cal:.0f} kcal  \n🍞 {p_c:.1f}g | 🥩 {p_p:.1f}g | 🥑 {p_f:.1f}g")
                    else:
                        st.error("Errore %")

            tot_perc_consumata = sum([porzioni_perc[i] for i in porzioni_selezionate])
            rt_consumo_vassoio = tot_perc_consumata / 100.0
            
            st.write("")
            if rt_consumo_vassoio > 0 and perc_rimanente >= 0:
                st.success(f"💡 Stai registrando nel diario il **{tot_perc_consumata:.1f}%** dell'intero vassoio (Peso che andrai a consumare: **{m_peso_tot * rt_consumo_vassoio:.1f} g**)")
                cm_cal, cm2, cm1, cm3, cm4, cm5 = st.columns(6)
                cm_cal.markdown(f"**Calorie**\n\n{m_cal_tot * rt_consumo_vassoio:.0f} kcal")
                cm2.markdown(f"**Carb.**\n\n{m_c_tot * rt_consumo_vassoio:.1f} g")
                cm1.markdown(f"**Prot.**\n\n{m_p_tot * rt_consumo_vassoio:.1f} g")
                cm3.markdown(f"**Grassi**\n\n{m_f_tot * rt_consumo_vassoio:.1f} g")
                cm4.markdown(f"**Saturi**\n\n{m_sat_tot * rt_consumo_vassoio:.1f} g")
                cm5.markdown(f"**Fibre**\n\n{m_fib_tot * rt_consumo_vassoio:.1f} g")
            elif perc_rimanente < 0:
                st.error("Impossibile procedere: sistema le percentuali delle porzioni.")
            else:
                st.warning("Seleziona almeno una porzione da mangiare tra quelle disponibili.")
            
            st.divider()
            
            st.markdown("### 3️⃣ Salvataggio")
            nome_gruppo = st.text_input("Vuoi raggruppare questi elementi in un'unica voce? Inserisci un nome (es. 'Mix Proteico') o lascia vuoto per salvarli separatamente:", "")
            
            if rt_consumo_vassoio > 0 and perc_rimanente >= 0:
                if nome_gruppo.strip():
                    dettaglio = ", ".join(ingredienti_list)
                    rows_to_add.append({
                        "ID": uuid.uuid4().hex, "Data": str(data_sel), "Pasto": pasto_sel,
                        "Elemento": f"📦 {nome_gruppo.strip()} [{dettaglio}]", "Quantita": tot_perc_consumata, "Unita": "%",
                        "Calorie": m_cal_tot * rt_consumo_vassoio, "Carboidrati": m_c_tot * rt_consumo_vassoio, "Proteine": m_p_tot * rt_consumo_vassoio,
                        "Grassi": m_f_tot * rt_consumo_vassoio, "Saturi": m_sat_tot * rt_consumo_vassoio, "Fibre": m_fib_tot * rt_consumo_vassoio, "User_ID": USER_ID,
                        "TGT_Cal": tgt_cal, "TGT_C": tgt_c, "TGT_P": tgt_p, "TGT_F": tgt_f
                    })
                else:
                    for item in st.session_state.diario_multi_items:
                        cal, p, c, f, fib, sat, _, _, _ = MACROS_DB[item["nome"]]
                        peso_effettivo = item['quantita'] * item.get("peso_pz", 0.0) if item["unita"] == "pz" else item['quantita']
                        
                        cal_i = (cal / 100) * peso_effettivo * rt_consumo_vassoio
                        c_i = (c / 100) * peso_effettivo * rt_consumo_vassoio
                        p_i = (p / 100) * peso_effettivo * rt_consumo_vassoio
                        f_i = (f / 100) * peso_effettivo * rt_consumo_vassoio
                        sat_i = (sat / 100) * peso_effettivo * rt_consumo_vassoio
                        fib_i = (fib / 100) * peso_effettivo * rt_consumo_vassoio
                        
                        p_cotto_str = f" (Cotto)" if item.get("is_cotto") else ""
                        qty_finale_salvata = item['quantita'] * rt_consumo_vassoio
                        
                        rows_to_add.append({
                            "ID": uuid.uuid4().hex, "Data": str(data_sel), "Pasto": pasto_sel,
                            "Elemento": f"🛒 {item['nome']}{p_cotto_str}", "Quantita": qty_finale_salvata, "Unita": item['unita'],
                            "Calorie": cal_i, "Carboidrati": c_i, "Proteine": p_i,
                            "Grassi": f_i, "Saturi": sat_i, "Fibre": fib_i, "User_ID": USER_ID,
                            "TGT_Cal": tgt_cal, "TGT_C": tgt_c, "TGT_P": tgt_p, "TGT_F": tgt_f
                        })
                ready_to_add = True

    # ---------------------------------------------------------
    # FLUSSO 3: RICETTA LIBERA AL VOLO (NON SALVATA)
    # ---------------------------------------------------------
    elif tipo_inserimento_diario == "⏱️ Ricetta Libera (Al volo)":
        st.write("Aggiungi gli ingredienti per calcolare una preparazione veloce.")
        
        def update_lib_from_selection():
            ing = st.session_state.get("ing_lib_sel")
            if ing and ing != "-- Seleziona --":
                m_name, m_cal, m_p, m_c, m_f, m_fib, m_sat, m_var, peso_pz, unita_def = get_macros_and_match(ing)
                st.session_state.unit_lib_val = unita_def
                st.session_state.lib_pz_w = peso_pz if peso_pz > 0 else 0.0

        c_ing, c_qta, c_unit, c_pz, c_btn = st.columns([3, 1, 1, 1, 1.5])
        ing_libero = c_ing.selectbox("Ingrediente", ["-- Seleziona --"] + sorted(list(MACROS_DB.keys())), key="ing_lib_sel", on_change=update_lib_from_selection)
        qta_libera = c_qta.number_input("Quantità", min_value=0.0, step=10.0, key="qta_lib_val", value=None)
        idx_u_lib = ["g", "ml", "pz"].index(st.session_state.get("unit_lib_val", "g")) if st.session_state.get("unit_lib_val") in ["g", "ml", "pz"] else 0
        unit_libera = c_unit.selectbox("Unità", options=["g", "ml", "pz"], key="unit_lib_val", index=idx_u_lib)
        
        if unit_libera == "pz": 
            default_pz_lib = MACROS_DB[ing_libero][7] if ing_libero != "-- Seleziona --" and ing_libero in MACROS_DB else 0.0
            pz_w_lib = c_pz.number_input("Peso 1pz (g)", min_value=0.0, step=1.0, value=float(default_pz_lib), key="lib_pz_w")
        else: 
            pz_w_lib = 0.0

        if ing_libero != "-- Seleziona --" and qta_libera is not None and qta_libera > 0:
            cal_l, p_l, c_l, f_l, _, _, _, _, _ = MACROS_DB[ing_libero]
            peso_l = qta_libera * pz_w_lib if unit_libera == "pz" else qta_libera
            st.markdown(f"<div style='color:gray; font-size:14px; margin-top:-10px; margin-bottom:10px;'>📊 <b>Valori per {peso_l:.1f}g:</b> {cal_l*peso_l/100:.0f} kcal | C: {c_l*peso_l/100:.1f}g | P: {p_l*peso_l/100:.1f}g | G: {f_l*peso_l/100:.1f}g</div>", unsafe_allow_html=True)
            
        def on_add_libero():
            ing = st.session_state.get("ing_lib_sel", "-- Seleziona --")
            qta = st.session_state.get("qta_lib_val")
            unit = st.session_state.get("unit_lib_val")
            pz_w_val = st.session_state.get("lib_pz_w", 0.0)
            
            if ing != "-- Seleziona --" and qta is not None and qta > 0 and unit is not None:
                st.session_state.temp_recipe_diario.append({
                    "id": uuid.uuid4().hex, "nome": ing, "quantita": float(qta),
                    "unita": unit, "peso_pz": float(pz_w_val)
                })
                st.session_state.ing_lib_sel = "-- Seleziona --"
                st.session_state.qta_lib_val = None

        can_add_lib = True
        if unit_libera == "pz" and pz_w_lib <= 0: 
            can_add_lib = False

        with c_btn:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            st.button("➕ Aggiungi Ingrediente", use_container_width=True, on_click=on_add_libero, disabled=(not can_add_lib or qta_libera is None or qta_libera <= 0))
                
        if st.session_state.temp_recipe_diario:
            st.markdown("---")
            w_raw_tot = m_cal_tot = m_p_tot = m_c_tot = m_f_tot = m_sat_tot = m_fib_tot = 0.0
            
            for i, ing in enumerate(st.session_state.temp_recipe_diario):
                c1, c2, c3 = st.columns([0.6, 0.3, 0.1])
                new_qty = c2.number_input("Q.tà", min_value=0.0, value=float(ing['quantita']), step=1.0 if ing['unita'] == 'pz' else 5.0, key=f"edit_lib_{ing['id']}", label_visibility="collapsed")
                
                if new_qty != ing['quantita']: st.session_state.temp_recipe_diario[i]['quantita'] = new_qty
                if c3.button("❌", key=f"del_lib_{ing['id']}"):
                    st.session_state.temp_recipe_diario = [item for item in st.session_state.temp_recipe_diario if item['id'] != ing['id']]
                    st.rerun()
                
                cal, p, c, f, fib, sat, _, _, _ = MACROS_DB[ing['nome']]
                peso_eff = new_qty * ing.get("peso_pz", 0.0) if ing['unita'] == 'pz' else new_qty
                
                w_raw_tot += peso_eff
                cal_i = (cal / 100) * peso_eff
                p_i = (p / 100) * peso_eff
                c_i = (c / 100) * peso_eff
                f_i = (f / 100) * peso_eff
                
                m_cal_tot += cal_i; m_p_tot += p_i; m_c_tot += c_i; m_f_tot += f_i
                m_sat_tot += (sat / 100) * peso_eff; m_fib_tot += (fib / 100) * peso_eff
                
                c1.write(f"🔹 **{ing['nome']}** *(Cal: {cal_i:.0f} | C: {c_i:.1f}g | P: {p_i:.1f}g | G: {f_i:.1f}g)*")
                
            nome_libera = st.text_input("Dai un nome per ricordarla nel diario:", "Pasto al volo")
            peso_cotto_libero = st.number_input("Peso cotto finale", min_value=1.0, value=float(w_raw_tot))
            
            st.info(f"⚖️ **Report:** Peso a crudo: **{w_raw_tot:.1f} g** | Cotto/Finito: **{peso_cotto_libero:.1f} g**")
            
            st.markdown("#### 🥧 Resa e Porzioni")
            
            num_porzioni_lib = st.number_input("In quante porzioni totali dividi questa preparazione? (max 5)", min_value=1, max_value=5, step=1, value=1, key="num_porz_lib")
            
            porzioni_perc_lib = []
            perc_rimanente_lib = 100.0
            
            if num_porzioni_lib == 1:
                porzioni_perc_lib = [100.0]
                st.info("La preparazione è considerata come 1 singola porzione (100%).")
            else:
                st.write("Imposta la % per ogni porzione (l'ultima è calcolata in automatico):")
                cols_perc_lib = st.columns(num_porzioni_lib)
                somma_parziale_lib = 0.0
                
                for i in range(num_porzioni_lib - 1):
                    with cols_perc_lib[i]:
                        default_p_lib = 100.0 / num_porzioni_lib
                        p_val_lib = st.number_input(f"% Porz. {i+1}", min_value=0.0, max_value=100.0, value=float(default_p_lib), step=1.0, key=f"perc_p_lib_{i}")
                        porzioni_perc_lib.append(p_val_lib)
                        somma_parziale_lib += p_val_lib
                
                perc_rimanente_lib = 100.0 - somma_parziale_lib
                porzioni_perc_lib.append(perc_rimanente_lib)
                
                with cols_perc_lib[-1]:
                    st.text_input(f"% Porz. {num_porzioni_lib} (Resto)", value=f"{perc_rimanente_lib:.1f}%", disabled=True, key=f"resto_lib_txt")
                
                if perc_rimanente_lib < 0:
                    st.error("⚠️ Attenzione: La somma delle percentuali supera il 100%. Riduci i valori.")

            st.divider()
            
            st.markdown("### 2️⃣ Quali porzioni stai mangiando?")
            
            porzioni_selezionate_lib = []
            cols_chk_lib = st.columns(num_porzioni_lib)
            
            for i in range(num_porzioni_lib):
                perc = porzioni_perc_lib[i]
                with cols_chk_lib[i]:
                    if perc >= 0:
                        mangio = st.checkbox(f"🍽️ Mangio Porz. {i+1} ({perc:.1f}%)", value=(i==0), key=f"mangio_chk_lib_{i}")
                        if mangio:
                            porzioni_selezionate_lib.append(i)
                        
                        # Calcolo peso, calorie e MACROS della singola porzione
                        p_peso = peso_cotto_libero * (perc / 100.0)
                        p_cal = m_cal_tot * (perc / 100.0)
                        p_c = m_c_tot * (perc / 100.0)
                        p_p = m_p_tot * (perc / 100.0)
                        p_f = m_f_tot * (perc / 100.0)
                        
                        st.caption(f"⚖️ {p_peso:.1f}g | 🔥 {p_cal:.0f} kcal  \n🍞 {p_c:.1f}g | 🥩 {p_p:.1f}g | 🥑 {p_f:.1f}g")
                    else:
                        st.error("Errore %")

            tot_perc_consumata_lib = sum([porzioni_perc_lib[i] for i in porzioni_selezionate_lib])
            rt_consumo_lib = tot_perc_consumata_lib / 100.0
            
            st.write("")
            if rt_consumo_lib > 0 and perc_rimanente_lib >= 0:
                st.success(f"💡 Stai registrando nel diario il **{tot_perc_consumata_lib:.1f}%** dell'intera preparazione (Peso consumato: **{peso_cotto_libero * rt_consumo_lib:.1f} g**)")
                cm_cal, cm2, cm1, cm3, cm4, cm5 = st.columns(6)
                cm_cal.markdown(f"**Calorie**\n\n{m_cal_tot * rt_consumo_lib:.0f} kcal")
                cm2.markdown(f"**Carb.**\n\n{m_c_tot * rt_consumo_lib:.1f} g")
                cm1.markdown(f"**Prot.**\n\n{m_p_tot * rt_consumo_lib:.1f} g")
                cm3.markdown(f"**Grassi**\n\n{m_f_tot * rt_consumo_lib:.1f} g")
                cm4.markdown(f"**Saturi**\n\n{m_sat_tot * rt_consumo_lib:.1f} g")
                cm5.markdown(f"**Fibre**\n\n{m_fib_tot * rt_consumo_lib:.1f} g")
            elif perc_rimanente_lib < 0:
                st.error("Impossibile procedere: sistema le percentuali delle porzioni.")
            else:
                st.warning("Seleziona almeno una porzione da mangiare tra quelle disponibili.")
            
            st.divider()
            
            st.markdown("### 3️⃣ Salvataggio")
            
            if rt_consumo_lib > 0 and perc_rimanente_lib >= 0:
                dettaglio_lib = ", ".join([f"{ing['quantita']:g}{ing['unita']} {ing['nome']}" for ing in st.session_state.temp_recipe_diario])
                elemento_inserito = f"⏱️ {nome_libera} [{dettaglio_lib}]"

                rows_to_add.append({
                    "ID": uuid.uuid4().hex, "Data": str(data_sel), "Pasto": pasto_sel,
                    "Elemento": elemento_inserito, "Quantita": tot_perc_consumata_lib, "Unita": "%",
                    "Calorie": m_cal_tot * rt_consumo_lib, "Carboidrati": m_c_tot * rt_consumo_lib, "Proteine": m_p_tot * rt_consumo_lib,
                    "Grassi": m_f_tot * rt_consumo_lib, "Saturi": m_sat_tot * rt_consumo_lib, "Fibre": m_fib_tot * rt_consumo_lib, "User_ID": USER_ID,
                    "TGT_Cal": tgt_cal, "TGT_C": tgt_c, "TGT_P": tgt_p, "TGT_F": tgt_f
                })
                ready_to_add = True

    # =========================================================
    # BOTTONE SALVATAGGIO UNIFICATO NEL DIARIO
    # =========================================================
    st.write("")
    if ready_to_add:
        if st.button("➕ Registra nel Diario", type="primary", use_container_width=True):
            with st.spinner("Salvataggio in corso..."):
                try:
                    df_diario = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Diario", ttl=0)
                    if 'User_ID' not in df_diario.columns: df_diario['User_ID'] = ADMIN_ID
                    
                    expected = ["ID", "Data", "Pasto", "Elemento", "Quantita", "Unita", "Calorie", "Carboidrati", "Proteine", "Grassi", "Saturi", "Fibre", "User_ID", "TGT_Cal", "TGT_C", "TGT_P", "TGT_F"]
                    for c in expected:
                        if c not in df_diario.columns: df_diario[c] = None
                    df_diario = df_diario[expected]
                    
                    nuove_righe = pd.DataFrame(rows_to_add)
                    df_diario_upd = pd.concat([df_diario, nuove_righe], ignore_index=True)
                    conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Diario", data=df_diario_upd)
                    st.cache_data.clear()
                    
                    st.session_state.diario_multi_items = []; st.session_state.temp_recipe_diario = [] 
                    for k in ['vassoio_ing_scelto', 'vassoio_qta', 'vassoio_unit', 'chk_cotto', 'ing_lib_sel', 'qta_lib_val', 'unit_lib_val']:
                        st.session_state.pop(k, None)
                    st.success("✅ Pasto aggiunto al tuo diario personale!")
                    st.rerun()
                except Exception as e:
                    st.error(f"⚠️ Errore di salvataggio. Dettaglio: {e}")

    # ==========================================
    # 📊 REPORT E STORICO GIORNALIERO (Filtrato per Utente)
    # ==========================================
    st.divider()
    
    try:
        df_diario_completo = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Diario", ttl=600)
        if 'User_ID' not in df_diario_completo.columns: df_diario_completo['User_ID'] = ADMIN_ID
        
        expected = ["ID", "Data", "Pasto", "Elemento", "Quantita", "Unita", "Calorie", "Carboidrati", "Proteine", "Grassi", "Saturi", "Fibre", "User_ID", "TGT_Cal", "TGT_C", "TGT_P", "TGT_F"]
        for c in expected:
            if c not in df_diario_completo.columns: df_diario_completo[c] = None
        df_diario_completo = df_diario_completo[expected]
        
        df_diario = df_diario_completo[df_diario_completo['User_ID'] == USER_ID]
        
        # Recupera Obiettivi Attuali dal Profilo per la riga di OGGI (per le barre del Giorno Attivo)
        tgt_cal = tgt_c = tgt_p = tgt_f = 0.0
        try:
            df_prof = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Profilo", ttl=600)
            u_prof = df_prof[df_prof['User_ID'] == USER_ID]
            if not u_prof.empty:
                tgt_cal = float(u_prof.iloc[0].get('TGT_Cal', 0) or 0)
                tgt_c = float(u_prof.iloc[0].get('TGT_C', 0) or 0)
                tgt_p = float(u_prof.iloc[0].get('TGT_P', 0) or 0)
                tgt_f = float(u_prof.iloc[0].get('TGT_F', 0) or 0)
        except: pass

        def render_prog(col, label, curr, tgt, unit):
            with col:
                if tgt > 0:
                    perc = curr / tgt
                    diff = tgt - curr
                    st.progress(min(max(perc, 0.0), 1.0))
                    
                    if diff >= 0:
                        st.markdown(f"{label}: **{curr:.0f}** / {tgt:.0f} {unit}")
                        st.caption(f"📉 Mancano: **{diff:.0f}** {unit} ({(diff/tgt)*100:.1f}%)")
                    else:
                        st.markdown(f"<span style='color:#FF4B4B;'>{label}: <b>{curr:.0f}</b> / {tgt:.0f} {unit}</span>", unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#FF4B4B; font-size:14px;'>🚨 Superato di: <b>{abs(diff):.0f}</b> {unit} (+{(perc*100)-100:.1f}%)</span>", unsafe_allow_html=True)
                else:
                    st.write(f"{label}: {curr:.0f}")

        def get_status_emoji(val, tgt):
            if pd.isna(tgt) or tgt <= 0: return ""
            if val < tgt * 0.90: return "🟨"
            elif val > tgt * 1.05: return "🚨"
            else: return "✅"

        # 1. GIORNO ATTIVO
        if str(data_sel) == str(pd.to_datetime('today').date()): etichetta_giorno = "Oggi"
        elif str(data_sel) == str((pd.to_datetime('today') - pd.Timedelta(days=1)).date()): etichetta_giorno = "Ieri"
        else: etichetta_giorno = "Data"
        
        st.markdown(f"#### 🔵 {etichetta_giorno}: {data_sel.strftime('%d/%m/%Y')}")
        df_oggi = df_diario[df_diario['Data'] == str(data_sel)]
        
        if not df_oggi.empty:
            t_cal = df_oggi['Calorie'].sum(); t_c = df_oggi['Carboidrati'].sum(); t_p = df_oggi['Proteine'].sum(); t_f = df_oggi['Grassi'].sum()
            
            if tgt_cal > 0:
                st.markdown("##### 🎯 Progresso rispetto ai tuoi obiettivi:")
                cp1, cp2, cp3, cp4 = st.columns(4)
                render_prog(cp1, "🔥 Cal", t_cal, tgt_cal, "kcal")
                render_prog(cp2, "🍞 Carb", t_c, tgt_c, "g")
                render_prog(cp3, "🥩 Prot", t_p, tgt_p, "g")
                render_prog(cp4, "🥑 Gras", t_f, tgt_f, "g")
                st.write("")
            else:
                cm1, cm2, cm3, cm4 = st.columns(4)
                cm1.metric("🔥 Calorie Totali", f"{t_cal:.0f} kcal")
                cm2.metric("🍞 Carboidrati", f"{t_c:.1f} g")
                cm3.metric("🥩 Proteine", f"{t_p:.1f} g")
                cm4.metric("🥑 Grassi", f"{t_f:.1f} g")
            
            st.write("")
            for pasto in ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]:
                df_pasto = df_oggi[(df_oggi['Pasto'] == pasto) | (df_oggi['Pasto'] == "Spuntino Mattina" if pasto == "Spuntino" else False)]
                if not df_pasto.empty:
                    t_cal_p = df_pasto['Calorie'].sum(); t_c_p = df_pasto['Carboidrati'].sum(); t_p_p = df_pasto['Proteine'].sum(); t_f_p = df_pasto['Grassi'].sum()
                    with st.expander(f"🍽️ {pasto.upper()} (Tot: {t_cal_p:.0f} kcal | C: {t_c_p:.1f}g | P: {t_p_p:.1f}g | G: {t_f_p:.1f}g)", expanded=False):
                        for _, row in df_pasto.iterrows():
                            c_text, c_del = st.columns([0.90, 0.10])
                            c_text.write(f"- **{row['Quantita']:.1f} {row['Unita']}** di {row['Elemento']} *(Cal: {row['Calorie']:.0f} | C: {row['Carboidrati']:.1f} | P: {row['Proteine']:.1f} | G: {row['Grassi']:.1f})*")
                            
                            if st.session_state.get('confirm_del_diario') != row['ID']:
                                if c_del.button("❌", key=f"del_oggi_{row['ID']}"):
                                    st.session_state.confirm_del_diario = row['ID']
                                    st.rerun()
                            else:
                                st.warning(f"⚠️ Vuoi eliminare '{row['Elemento']}'?")
                                cy, cn = st.columns(2)
                                if cy.button("🚨 Sì", key=f"yes_oggi_{row['ID']}", type="primary"):
                                    df_to_delete = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Diario")
                                    df_to_delete = df_to_delete[df_to_delete.iloc[:, 0] != row['ID']]
                                    conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Diario", data=df_to_delete)
                                    st.cache_data.clear()
                                    st.session_state.confirm_del_diario = None
                                    st.rerun()
                                if cn.button("❌ No", key=f"no_oggi_{row['ID']}"):
                                    st.session_state.confirm_del_diario = None
                                    st.rerun()
        else: st.info("Nessun pasto registrato per la data selezionata.")

        st.divider()
        st.markdown("### 📊 Storico ed Analisi Report")
        
        st.markdown(
            """
            <style>
            .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
                font-size: 1.15rem !important;
                font-weight: bold !important;
            }
            </style>
            """, unsafe_allow_html=True
        )

        tab_report, tab_storico, tab_planner = st.tabs(["📈 Statistiche e Report", "🗓️ Storico Giornaliero", "📆 Meal Planning (Futuro)"])

        with tab_report:
            c_date1, c_date2 = st.columns([1, 2])
            rep_mode = c_date1.radio("Periodo di analisi:", ["Oggi", "Ieri", "Ultimi 7 gg", "Ultimi 30 gg", "Personalizzato"], index=0, horizontal=True)
            
            oggi = pd.to_datetime('today').date()
            if rep_mode == "Oggi": start_date = end_date = oggi
            elif rep_mode == "Ieri": start_date = end_date = oggi - datetime.timedelta(days=1)
            elif rep_mode == "Ultimi 7 gg": start_date = oggi - datetime.timedelta(days=7); end_date = oggi
            elif rep_mode == "Ultimi 30 gg": start_date = oggi - datetime.timedelta(days=30); end_date = oggi
            else:
                sel_dates = c_date2.date_input("Seleziona intervallo:", [oggi, oggi])
                if len(sel_dates) == 2: start_date, end_date = sel_dates
                else: start_date, end_date = oggi, oggi

            df_diario['Data_DT'] = pd.to_datetime(df_diario['Data'], format='%Y-%m-%d', errors='coerce').dt.date
            mask_date = (df_diario['Data_DT'] >= start_date) & (df_diario['Data_DT'] <= end_date)
            df_rep_base = df_diario[mask_date].copy()
            
            if not df_rep_base.empty:
                st.divider()
                st.markdown("#### 🍽️ Analisi Dinamica per Pasti")
                pasti_disponibili = ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena", "Spuntino Mattina"]
                pasti_presenti = [p for p in pasti_disponibili if p in df_rep_base['Pasto'].unique() or (p=="Spuntino" and "Spuntino Mattina" in df_rep_base['Pasto'].unique())]
                if not pasti_presenti: pasti_presenti = df_rep_base['Pasto'].unique().tolist()
                
                pasti_selezionati = st.multiselect("Quali pasti vuoi analizzare?", options=pasti_presenti, default=pasti_presenti)
                pasti_filter = list(pasti_selezionati)
                if "Spuntino" in pasti_filter and "Spuntino Mattina" not in pasti_filter: pasti_filter.append("Spuntino Mattina")
                    
                df_rep = df_rep_base[df_rep_base['Pasto'].isin(pasti_filter)]
                
                if not df_rep.empty:
                    giorni_totali = df_rep['Data'].nunique()
                    st.write(f"📊 **Medie calcolate dal {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')} su {giorni_totali} giorni attivi:**")
                    
                    tot_cal = df_rep['Calorie'].sum()
                    tot_c = df_rep['Carboidrati'].sum(); tot_p = df_rep['Proteine'].sum(); tot_f = df_rep['Grassi'].sum()
                    
                    media_cal = tot_cal / giorni_totali
                    media_c = tot_c / giorni_totali
                    media_p = tot_p / giorni_totali
                    media_f = tot_f / giorni_totali
                    
                    if tgt_cal > 0:
                        st.markdown("##### 🎯 Media Giornaliera rispetto ai tuoi Obiettivi:")
                        c_r1, c_r2, c_r3, c_r4 = st.columns(4)
                        render_prog(c_r1, "🔥 Cal Medie", media_cal, tgt_cal, "kcal")
                        render_prog(c_r2, "🍞 Carb Medi", media_c, tgt_c, "g")
                        render_prog(c_r3, "🥩 Prot Medie", media_p, tgt_p, "g")
                        render_prog(c_r4, "🥑 Gras Medi", media_f, tgt_f, "g")
                        st.write("")
                    else:
                        c_r1, c_r2, c_r3, c_r4 = st.columns(4)
                        c_r1.metric("🔥 Calorie Totali", f"{tot_cal:.0f} kcal", f"Media: {media_cal:.0f} /gg")
                        c_r2.metric("🍞 Carb. Totali", f"{tot_c:.1f} g", f"Media: {media_c:.1f} /gg")
                        c_r3.metric("🥩 Prot. Totali", f"{tot_p:.1f} g", f"Media: {media_p:.1f} /gg")
                        c_r4.metric("🥑 Grassi Totali", f"{tot_f:.1f} g", f"Media: {media_f:.1f} /gg")
                    
                    st.write("")
                    c_chart1, c_chart2 = st.columns([1, 1.8])
                    with c_chart1:
                        st.markdown("**Ripartizione Macronutrienti (g)**")
                        if tot_c + tot_p + tot_f > 0:
                            fig_pie = px.pie(names=['Carboidrati', 'Proteine', 'Grassi'], values=[tot_c, tot_p, tot_f], color_discrete_sequence=['#FFA07A', '#87CEFA', '#98FB98'], hole=0.4)
                            fig_pie.update_layout(margin=dict(t=20, b=20, l=0, r=0), height=300, showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5))
                            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                            st.plotly_chart(fig_pie, use_container_width=True)
                        else: st.info("Dati macros insufficienti per il grafico a torta.")
                            
                    with c_chart2:
                        st.markdown("**Andamento Giornaliero Macros**")
                        df_trend = df_rep.groupby('Data')[['Carboidrati', 'Proteine', 'Grassi']].sum().reset_index()
                        df_trend['Data'] = pd.to_datetime(df_trend['Data'])
                        df_trend = df_trend.sort_values('Data')
                        
                        fig_line = px.line(df_trend, x='Data', y=['Carboidrati', 'Proteine', 'Grassi'], color_discrete_map={'Carboidrati':'#FFA07A', 'Proteine':'#87CEFA', 'Grassi':'#98FB98'}, markers=True)
                        fig_line.update_layout(xaxis_title="", yaxis_title="Grammi (g)", legend_title="", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(t=20, b=20, l=0, r=0), height=300, hovermode="x unified")
                        st.plotly_chart(fig_line, use_container_width=True)
                        
                    with st.expander("📅 Vedi Tabella Sintetica Giornaliera"):
                        df_day = df_rep.groupby('Data')[['Calorie', 'Carboidrati', 'Proteine', 'Grassi']].sum().reset_index()
                        df_day = df_day.sort_values('Data', ascending=False)
                        st.dataframe(df_day.style.format({"Calorie": "{:.0f}", "Carboidrati": "{:.1f}", "Proteine": "{:.1f}", "Grassi": "{:.1f}"}), use_container_width=True, hide_index=True)

                    st.divider()
                    st.markdown("### 📖 Dettaglio Completo dei Pasti Selezionati")
                    giorni_report = df_rep['Data'].dropna().unique()
                    giorni_report_sorted = sorted(giorni_report, reverse=True)
                    
                    for d in giorni_report_sorted:
                        df_giorno = df_rep[df_rep['Data'] == d]
                        t_cal_storico = df_giorno['Calorie'].sum()
                        t_c_s = df_giorno['Carboidrati'].sum()
                        t_p_s = df_giorno['Proteine'].sum()
                        t_f_s = df_giorno['Grassi'].sum()
                        d_obj = pd.to_datetime(d).strftime('%d/%m/%Y')
                        
                        # Estrae l'Obiettivo Storico congelato per quel giorno (se presente)
                        h_tgt_cal = df_giorno['TGT_Cal'].max() if 'TGT_Cal' in df_giorno.columns else 0.0
                        if pd.isna(h_tgt_cal) or h_tgt_cal == 0: h_tgt_cal = tgt_cal
                        h_tgt_c = df_giorno['TGT_C'].max() if 'TGT_C' in df_giorno.columns else 0.0
                        if pd.isna(h_tgt_c) or h_tgt_c == 0: h_tgt_c = tgt_c
                        h_tgt_p = df_giorno['TGT_P'].max() if 'TGT_P' in df_giorno.columns else 0.0
                        if pd.isna(h_tgt_p) or h_tgt_p == 0: h_tgt_p = tgt_p
                        h_tgt_f = df_giorno['TGT_F'].max() if 'TGT_F' in df_giorno.columns else 0.0
                        if pd.isna(h_tgt_f) or h_tgt_f == 0: h_tgt_f = tgt_f
                        
                        titolo_storico = f"📅 {d_obj} - Totale: {t_cal_storico:.0f} kcal"
                        if h_tgt_cal > 0:
                            titolo_storico += f" / {h_tgt_cal:.0f} kcal {get_status_emoji(t_cal_storico, h_tgt_cal)}"
                            
                        with st.expander(titolo_storico):
                            if h_tgt_cal > 0:
                                st.markdown(f"**Macros Consumati:** Carboidrati: {t_c_s:.1f}g/{h_tgt_c:.0f}g {get_status_emoji(t_c_s, h_tgt_c)} | Proteine: {t_p_s:.1f}g/{h_tgt_p:.0f}g {get_status_emoji(t_p_s, h_tgt_p)} | Grassi: {t_f_s:.1f}g/{h_tgt_f:.0f}g {get_status_emoji(t_f_s, h_tgt_f)}")
                            else:
                                st.markdown(f"**Macros:** Carboidrati: {t_c_s:.1f}g | Proteine: {t_p_s:.1f}g | Grassi: {t_f_s:.1f}g")
                            st.write("")
                            for pasto in ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]:
                                df_pasto_s = df_giorno[(df_giorno['Pasto'] == pasto) | (df_giorno['Pasto'] == "Spuntino Mattina" if pasto == "Spuntino" else False)]
                                if not df_pasto_s.empty:
                                    t_cal_p = df_pasto_s['Calorie'].sum(); t_c_p = df_pasto_s['Carboidrati'].sum(); t_p_p = df_pasto_s['Proteine'].sum(); t_f_p = df_pasto_s['Grassi'].sum()
                                    with st.expander(f"🍽️ {pasto.upper()} (Tot: {t_cal_p:.0f} kcal | C: {t_c_p:.1f}g | P: {t_p_p:.1f}g | G: {t_f_p:.1f}g)", expanded=False):
                                        for _, row in df_pasto_s.iterrows():
                                            c_text_s, c_del_s = st.columns([0.90, 0.10])
                                            c_text_s.write(f"- **{row['Quantita']:.1f} {row['Unita']}** di {row['Elemento']} *(Cal: {row['Calorie']:.0f} | C: {row['Carboidrati']:.1f} | P: {row['Proteine']:.1f} | G: {row['Grassi']:.1f})*")
                                            if c_del_s.button("❌", key=f"del_report_{row['ID']}"):
                                                st.info("Eliminazione veloce dal report. Usa la selezione del Giorno Attivo (in alto) per le conferme definitive.")

                else: st.warning("Nessun dato registrato per i pasti selezionati in questo periodo.")
            else: st.info("Nessun dato registrato nell'intervallo di date selezionato.")

        with tab_storico:
            altri_giorni = df_diario[df_diario['Data'] != str(data_sel)]['Data'].dropna().unique()
            oggi_str = str(pd.to_datetime('today').date())
            altri_giorni_passati = [d for d in altri_giorni if d <= oggi_str]
            altri_giorni_sorted = sorted(altri_giorni_passati, reverse=True)
            
            if len(altri_giorni_sorted) > 0:
                for d in altri_giorni_sorted:
                    df_giorno = df_diario[df_diario['Data'] == d]
                    t_cal_s = df_giorno['Calorie'].sum()
                    t_c_s = df_giorno['Carboidrati'].sum()
                    t_p_s = df_giorno['Proteine'].sum()
                    t_f_s = df_giorno['Grassi'].sum()
                    d_obj = pd.to_datetime(d).strftime('%d/%m/%Y')
                    
                    # Estrae l'Obiettivo Storico congelato per quel giorno (se presente)
                    h_tgt_cal = df_giorno['TGT_Cal'].max() if 'TGT_Cal' in df_giorno.columns else 0.0
                    if pd.isna(h_tgt_cal) or h_tgt_cal == 0: h_tgt_cal = tgt_cal
                    h_tgt_c = df_giorno['TGT_C'].max() if 'TGT_C' in df_giorno.columns else 0.0
                    if pd.isna(h_tgt_c) or h_tgt_c == 0: h_tgt_c = tgt_c
                    h_tgt_p = df_giorno['TGT_P'].max() if 'TGT_P' in df_giorno.columns else 0.0
                    if pd.isna(h_tgt_p) or h_tgt_p == 0: h_tgt_p = tgt_p
                    h_tgt_f = df_giorno['TGT_F'].max() if 'TGT_F' in df_giorno.columns else 0.0
                    if pd.isna(h_tgt_f) or h_tgt_f == 0: h_tgt_f = tgt_f
                    
                    titolo_storico = f"📅 {d_obj} - Totale: {t_cal_s:.0f} kcal"
                    if h_tgt_cal > 0:
                        titolo_storico += f" / {h_tgt_cal:.0f} kcal {get_status_emoji(t_cal_s, h_tgt_cal)}"
                        
                    with st.expander(titolo_storico):
                        if h_tgt_cal > 0:
                            st.markdown(f"**Macros Consumati:** Carboidrati: {t_c_s:.1f}g/{h_tgt_c:.0f}g {get_status_emoji(t_c_s, h_tgt_c)} | Proteine: {t_p_s:.1f}g/{h_tgt_p:.0f}g {get_status_emoji(t_p_s, h_tgt_p)} | Grassi: {t_f_s:.1f}g/{h_tgt_f:.0f}g {get_status_emoji(t_f_s, h_tgt_f)}")
                        else:
                            st.markdown(f"**Macros:** Carboidrati: {t_c_s:.1f}g | Proteine: {t_p_s:.1f}g | Grassi: {t_f_s:.1f}g")
                        st.write("")
                        for pasto in ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]:
                            df_pasto_s = df_giorno[(df_giorno['Pasto'] == pasto) | (df_giorno['Pasto'] == "Spuntino Mattina" if pasto == "Spuntino" else False)]
                            if not df_pasto_s.empty:
                                t_cal_p = df_pasto_s['Calorie'].sum(); t_c_p = df_pasto_s['Carboidrati'].sum(); t_p_p = df_pasto_s['Proteine'].sum(); t_f_p = df_pasto_s['Grassi'].sum()
                                with st.expander(f"🍽️ {pasto.upper()} (Tot: {t_cal_p:.0f} kcal | C: {t_c_p:.1f}g | P: {t_p_p:.1f}g | G: {t_f_p:.1f}g)", expanded=False):
                                    for _, row in df_pasto_s.iterrows():
                                        c_text_s, c_del_s = st.columns([0.90, 0.10])
                                        c_text_s.write(f"- **{row['Quantita']:.1f} {row['Unita']}** di {row['Elemento']} *(Cal: {row['Calorie']:.0f} | C: {row['Carboidrati']:.1f} | P: {row['Proteine']:.1f} | G: {row['Grassi']:.1f})*")
                                        if c_del_s.button("❌", key=f"del_storico_{row['ID']}"):
                                            st.info("Eliminazione veloce dallo storico. Usa la selezione giorno per conferme.")
            else:
                st.write("Nessun altro giorno salvato nel tuo storico.")

        with tab_planner:
            st.markdown("### 📆 I Tuoi Pasti Futuri")
            st.write("Usa il calendario in alto per registrare i tuoi pasti per i giorni a venire. Qui trovi il riepilogo della tua programmazione settimanale.")
            
            df_diario['Data_DT'] = pd.to_datetime(df_diario['Data'], format='%Y-%m-%d', errors='coerce').dt.date
            oggi_date = pd.to_datetime('today').date()
            df_futuro = df_diario[df_diario['Data_DT'] > oggi_date].copy()
            
            if not df_futuro.empty:
                giorni_futuri = sorted(df_futuro['Data'].unique())
                for d in giorni_futuri:
                    df_giorno = df_futuro[df_futuro['Data'] == d]
                    t_cal_storico = df_giorno['Calorie'].sum()
                    d_obj = pd.to_datetime(d)
                    nome_giorno = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"][d_obj.weekday()]
                    
                    with st.expander(f"📌 {nome_giorno} {d_obj.strftime('%d/%m/%Y')} - Pianificato: {t_cal_storico:.0f} kcal", expanded=True):
                        st.markdown(f"**Macros Previsti:** Carboidrati: {df_giorno['Carboidrati'].sum():.1f}g | Proteine: {df_giorno['Proteine'].sum():.1f}g | Grassi: {df_giorno['Grassi'].sum():.1f}g")
                        st.write("")
                        for pasto in ["Colazione", "Spuntino", "Pranzo", "Merenda", "Cena"]:
                            df_pasto_s = df_giorno[(df_giorno['Pasto'] == pasto) | (df_giorno['Pasto'] == "Spuntino Mattina" if pasto == "Spuntino" else False)]
                            if not df_pasto_s.empty:
                                st.markdown(f"**{pasto.upper()}** (Cal: {df_pasto_s['Calorie'].sum():.0f} kcal)")
                                for _, row in df_pasto_s.iterrows():
                                    st.write(f"- {row['Quantita']:.1f} {row['Unita']} di {row['Elemento']}")
                        st.write("")
                        if st.button(f"Vai a {nome_giorno}", key=f"btn_go_{d}"):
                            st.info("💡 Scorri in alto e seleziona questa data nel calendario per fare modifiche!")
            else:
                st.success("Non hai ancora pianificato nessun pasto per i prossimi giorni.")
                
    except Exception as e:
        st.info(f"Il tuo diario è vuoto o c'è un errore di configurazione in Sheets. {e}")

# ==========================================
# 🗄️ PAGINA 3: DATABASE PRODOTTI
# ==========================================
elif pagina_corrente == "🗄️ Database Prodotti":
    
    st.title("🗄️ Database Prodotti")
    st.markdown("#### *Gestisci i tuoi ingredienti, consulta la lista e importa dal web.* 🛒")
    st.write("")

    # --- FIX: RECUPERO IL DATAFRAME DALLA CACHE PER I PERMESSI ---
    try:
        df_db = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Macros", ttl=600)
        if 'User_ID' not in df_db.columns: df_db['User_ID'] = ADMIN_ID
    except:
        df_db = pd.DataFrame(columns=["Nome", "User_ID"])

    # 1. NUOVO ORDINE DELLE AZIONI
    azione_db = st.radio("Scegli un'azione:", [
        "📋 Archivio e Gestione Prodotti", 
        "➕ Aggiungi Nuovo (Web / Manuale)", 
        "🗂️ Duplica Esistente"
    ], horizontal=True)
    
    st.divider()

    # ---------------------------------------------------------
    # AZIONE 1: ARCHIVIO E GESTIONE PRODOTTI
    # ---------------------------------------------------------
    if azione_db == "📋 Archivio e Gestione Prodotti":
        
        st.markdown("### ✏️ Cerca e Modifica al volo")
        prodotto_mod = st.selectbox("Cerca qui il prodotto da gestire:", ["-- Seleziona --"] + sorted(list(MACROS_DB.keys())), key="sel_mod_db")
        
        if prodotto_mod != "-- Seleziona --":
            cal_m, p_m, c_m, f_m, fib_m, sat_m, var_m, peso_m, unita_m = MACROS_DB[prodotto_mod]
            
            # Controllo permessi
            is_global = not df_db[(df_db['Nome'].str.lower() == prodotto_mod.lower()) & (df_db['User_ID'] == ADMIN_ID)].empty
            can_edit = IS_ADMIN or not is_global
            
            if not can_edit:
                st.error("🔒 **Prodotto di Nutrilab.** Non hai i permessi per modificarlo o eliminarlo. Se vuoi personalizzarlo, vai nella scheda 'Duplica Esistente'.")
            
            st.write("")
            c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1.5, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1, 1, 1.2])
            
            mod_n = c1.text_input("Nome", value=prodotto_mod, key="mod_n", disabled=not can_edit)
            mod_cal = c2.number_input("Cal", value=float(cal_m), step=1.0, key="mod_cal", disabled=not can_edit)
            mod_c = c3.number_input("Carb", value=float(c_m), step=0.1, key="mod_c", disabled=not can_edit)
            mod_p = c4.number_input("Prot", value=float(p_m), step=0.1, key="mod_p", disabled=not can_edit)
            mod_f = c5.number_input("Gras", value=float(f_m), step=0.1, key="mod_f", disabled=not can_edit)
            mod_sat = c6.number_input("Sat", value=float(sat_m), step=0.1, key="mod_sat", disabled=not can_edit)
            mod_fib = c7.number_input("Fib", value=float(fib_m), step=0.1, key="mod_fib", disabled=not can_edit)
            mod_var = c8.number_input("% V.Cott", value=float(var_m), step=1.0, key="mod_var", disabled=not can_edit)
            mod_peso = c9.number_input("Peso 1pz", value=float(peso_m), step=1.0, key="mod_peso", disabled=not can_edit)
            
            idx_u_mod = ["g", "ml", "pz"].index(unita_m) if unita_m in ["g", "ml", "pz"] else 0
            mod_unita = c10.selectbox("Unità Default", options=["g", "ml", "pz"], index=idx_u_mod, key="mod_udef", disabled=not can_edit)
            
            if can_edit:
                st.write("")
                col_save, col_del = st.columns(2)
                if col_save.button("💾 Aggiorna Modifiche", type="primary", use_container_width=True):
                    with st.spinner("Aggiornamento in corso..."):
                        if mod_n.strip().lower() != prodotto_mod.lower():
                            elimina_da_cloud(prodotto_mod)
                        salva_su_cloud(mod_n, mod_cal, mod_p, mod_c, mod_f, mod_sat, mod_fib, mod_var, mod_peso, mod_unita)
                        st.success("✅ Prodotto aggiornato con successo!")
                        st.rerun()
                        
                if col_del.button("🗑️ Elimina Prodotto", type="secondary", use_container_width=True):
                    st.session_state.confirm_del_prod = prodotto_mod
                    
                if st.session_state.get('confirm_del_prod') == prodotto_mod:
                    st.warning(f"⚠️ Sei sicuro di voler eliminare definitivamente '{prodotto_mod}' dal tuo archivio?")
                    cy, cn = st.columns(2)
                    if cy.button("🚨 Sì, Elimina", type="primary"):
                        with st.spinner("Eliminazione in corso..."):
                            successo = elimina_da_cloud(prodotto_mod)
                            st.session_state.confirm_del_prod = None
                            if successo: st.success("✅ Prodotto eliminato!")
                            else: st.error("Errore nell'eliminazione.")
                            st.rerun()
                    if cn.button("❌ Annulla"):
                        st.session_state.confirm_del_prod = None
                        st.rerun()
        
        st.divider()
        
        st.markdown("### 📊 Panoramica del Database")
        st.write("Consulta tutti i prodotti disponibili. Clicca sulle intestazioni per ordinare dal maggiore al minore e viceversa.")
        
        # Generiamo un DataFrame pulito per la visualizzazione
        lista_view = []
        for n, macros in MACROS_DB.items():
            cal, p, c, f, fib, sat, var, pz_w, u_def = macros
            # Determina se il prodotto è dell'admin controllando il DataFrame originale
            user_owner = df_db[df_db['Nome'].str.lower() == n.lower()]['User_ID'].iloc[0] if not df_db[df_db['Nome'].str.lower() == n.lower()].empty else ADMIN_ID
            proprietario = "🌍 Nutrilab" if user_owner == ADMIN_ID else "👤 Personale"
            
            lista_view.append({
                "Nome Prodotto": n, "Calorie": cal, "Carboidrati": c, "Proteine": p, 
                "Grassi": f, "Unità": u_def, "Peso 1pz": pz_w, "% Cottura": var, "Proprietario": proprietario
            })
            
        df_view = pd.DataFrame(lista_view)
        
        # Mostra tabella interattiva (ordinabile e filtrabile dall'utente)
        st.dataframe(df_view, use_container_width=True, hide_index=True)

   # ---------------------------------------------------------
    # AZIONE 2: AGGIUNGI NUOVO (WEB / MANUALE)
    # ---------------------------------------------------------
    elif azione_db == "➕ Aggiungi Nuovo (Web / Manuale)":
        
        # --- FIX STREAMLIT: GESTIONE MESSAGGI E RESET CAMPI ---
        if st.session_state.get("do_clear_add"):
            st.session_state.add_n = ""
            st.session_state.add_cal = 0.0
            st.session_state.add_c = 0.0
            st.session_state.add_p = 0.0
            st.session_state.add_f = 0.0
            st.session_state.add_sat = 0.0
            st.session_state.add_fib = 0.0
            st.session_state.add_var = 0.0
            st.session_state.add_peso = 0.0
            st.session_state.add_udef = "g"
            st.session_state.do_clear_add = False
            
        if st.session_state.get("msg_add_ok"):
            st.success(st.session_state.msg_add_ok)
            st.session_state.msg_add_ok = ""
            
        st.markdown("### 🌐 Cerca sul Web o Inserisci Manualmente")
        c_search, c_btn, c_clear = st.columns([2.5, 1, 1])
        search_term = c_search.text_input("Cerca alimento (es. Mela, Pollo):", key="search_term_db")
        
        if c_btn.button("🔍 Cerca (Locale + Web)", use_container_width=True):
            if search_term:
                with st.spinner("Ricerca in corso..."):
                    trovato_loc, n_loc, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def = cerca_locale(search_term)
                    if trovato_loc:
                        st.session_state.add_n = n_loc; st.session_state.add_cal = float(cal); st.session_state.add_p = float(p)
                        st.session_state.add_c = float(c); st.session_state.add_f = float(f); st.session_state.add_fib = float(fib)
                        st.session_state.add_sat = float(sat); st.session_state.add_var = float(var_cott)
                        st.session_state.add_peso = float(peso_pz); st.session_state.add_udef = unita_def
                        st.success(f"✅ Prodotto già trovato nel Database Locale come '{n_loc}'!")
                    else:
                        trovato_web, cal, p, c, f, fib, sat, var_cott, peso_pz, unita_def = cerca_alimento_web(search_term)
                        if trovato_web:
                            st.session_state.add_n = search_term.title(); st.session_state.add_cal = float(cal); st.session_state.add_p = float(p)
                            st.session_state.add_c = float(c); st.session_state.add_f = float(f); st.session_state.add_fib = float(fib)
                            st.session_state.add_sat = float(sat); st.session_state.add_var = 0.0
                            st.session_state.add_peso = 0.0; st.session_state.add_udef = "g"
                            st.success(f"🌐 Prodotto trovato sul Web (OpenFoodFacts)! Verifica i dati prima di salvare.")
                        else:
                            st.session_state.add_n = search_term.title(); st.session_state.add_cal = 0.0; st.session_state.add_p = 0.0
                            st.session_state.add_c = 0.0; st.session_state.add_f = 0.0; st.session_state.add_fib = 0.0
                            st.session_state.add_sat = 0.0; st.session_state.add_var = 0.0; st.session_state.add_peso = 0.0; st.session_state.add_udef = "g"
                            st.warning("⚠️ Nessun risultato trovato. I campi sono stati preparati per l'inserimento manuale.")

        if c_clear.button("🧹 Svuota Campi", use_container_width=True):
            st.session_state.add_n = ""; st.session_state.add_cal = 0.0; st.session_state.add_p = 0.0
            st.session_state.add_c = 0.0; st.session_state.add_f = 0.0; st.session_state.add_sat = 0.0
            st.session_state.add_fib = 0.0; st.session_state.add_var = 0.0; st.session_state.add_peso = 0.0; st.session_state.add_udef = "g"
            st.rerun()

        st.write("")
        st.markdown("**Verifica e salva i valori (su 100g/ml) del nuovo prodotto:**")
        c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1.5, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1, 1, 1.2])
        
        db_n = c1.text_input("Nome", key="add_n")
        db_cal = c2.number_input("Cal", step=1.0, key="add_cal")
        db_c = c3.number_input("Carb", step=0.1, key="add_c")
        db_p = c4.number_input("Prot", step=0.1, key="add_p")
        db_f = c5.number_input("Gras", step=0.1, key="add_f")
        db_sat = c6.number_input("Sat", step=0.1, key="add_sat")
        db_fib = c7.number_input("Fib", step=0.1, key="add_fib")
        db_var = c8.number_input("% V.Cott", step=1.0, key="add_var")
        db_peso_pz = c9.number_input("Peso 1pz", step=1.0, key="add_peso")
        db_unita_def = c10.selectbox("Unità Default", options=["g", "ml", "pz"], key="add_udef")

        st.write("")
        if st.button("➕ Salva nel Database", type="primary"):
            if db_n:
                esiste_gia = db_n.strip().lower() in [k.lower() for k in MACROS_DB.keys()]
                
                if esiste_gia:
                    st.session_state.show_dup_warning = db_n
                else:
                    with st.spinner("Salvataggio in Cloud..."):
                        success = salva_su_cloud(db_n, db_cal, db_p, db_c, db_f, db_sat, db_fib, db_var, db_peso_pz, db_unita_def)
                        if success:
                            st.session_state.msg_add_ok = f"✅ '{db_n}' salvato nel tuo database personale!"
                            st.session_state.do_clear_add = True
                            st.rerun()
            else: 
                st.warning("Inserisci il nome del prodotto prima di salvare.")

        # Gestione Avviso Duplicato fuori dal pulsante principale
        if st.session_state.get("show_dup_warning") == db_n:
            st.error(f"⚠️ Attenzione! Esiste già un prodotto chiamato **'{db_n}'** nel database.")
            st.write("Se procedi, andrai a sovrascrivere/aggiornare i valori di quello esistente (se hai i permessi).")
            
            cy, cn = st.columns(2)
            if cy.button("🚨 Sì, Sovrascrivi", type="primary"):
                with st.spinner("Sovrascrittura in Cloud..."):
                    salva_su_cloud(db_n, db_cal, db_p, db_c, db_f, db_sat, db_fib, db_var, db_peso_pz, db_unita_def)
                    st.session_state.show_dup_warning = None
                    st.session_state.msg_add_ok = "✅ Prodotto aggiornato e salvato!"
                    st.session_state.do_clear_add = True
                    st.rerun()
            if cn.button("❌ No, annulla e cambia nome"):
                st.session_state.show_dup_warning = None
                st.rerun()

    # ---------------------------------------------------------
    # AZIONE 3: DUPLICA ESISTENTE
    # ---------------------------------------------------------
    elif azione_db == "🗂️ Duplica Esistente":
        
        # --- FIX STREAMLIT: GESTIONE MESSAGGI E RESET CAMPI ---
        if st.session_state.get("do_clear_dup"):
            st.session_state.dup_n = ""
            st.session_state.dup_cal = 0.0
            st.session_state.dup_c = 0.0
            st.session_state.dup_p = 0.0
            st.session_state.dup_f = 0.0
            st.session_state.dup_sat = 0.0
            st.session_state.dup_fib = 0.0
            st.session_state.dup_var = 0.0
            st.session_state.dup_peso = 0.0
            st.session_state.dup_udef = "g"
            st.session_state.do_clear_dup = False
            
        if st.session_state.get("msg_dup_ok"):
            st.success(st.session_state.msg_dup_ok)
            st.session_state.msg_dup_ok = ""
            
        st.markdown("### 🗂️ Usa un prodotto esistente come base")
        c_dup, c_btn_dup = st.columns([3, 1])
        prodotto_da_duplicare = c_dup.selectbox("Seleziona un prodotto dal database:", ["-- Seleziona --"] + sorted(list(MACROS_DB.keys())), key="dup_db_sel")
        
        with c_btn_dup:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Carica Valori Originali", use_container_width=True):
                if prodotto_da_duplicare != "-- Seleziona --":
                    cal, p, c, f, fib, sat, var, peso_db, unita_db = MACROS_DB[prodotto_da_duplicare]
                    
                    st.session_state.dup_n = prodotto_da_duplicare + " (Personalizzato)"
                    st.session_state.dup_cal = float(cal); st.session_state.dup_p = float(p)
                    st.session_state.dup_c = float(c); st.session_state.dup_f = float(f)
                    st.session_state.dup_sat = float(sat); st.session_state.dup_fib = float(fib)
                    st.session_state.dup_var = float(var); st.session_state.dup_peso = float(peso_db); st.session_state.dup_udef = unita_db
                    
                    st.success(f"✅ Valori di '{prodotto_da_duplicare}' caricati. Modifica il nome e salva la tua variante!")
                else: 
                    st.warning("Seleziona prima un prodotto dalla tendina.")

        st.write("")
        st.markdown("**Modifica i valori e salva come nuovo prodotto**")
        c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1.5, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1, 1, 1.2])
        
        db_n = c1.text_input("Nome Variante", key="dup_n")
        db_cal = c2.number_input("Cal", step=1.0, key="dup_cal")
        db_c = c3.number_input("Carb", step=0.1, key="dup_c")
        db_p = c4.number_input("Prot", step=0.1, key="dup_p")
        db_f = c5.number_input("Gras", step=0.1, key="dup_f")
        db_sat = c6.number_input("Sat", step=0.1, key="dup_sat")
        db_fib = c7.number_input("Fib", step=0.1, key="dup_fib")
        db_var = c8.number_input("% V.Cott", step=1.0, key="dup_var")
        db_peso_pz = c9.number_input("Peso 1pz", step=1.0, key="dup_peso")
        db_unita_def = c10.selectbox("Unità Default", options=["g", "ml", "pz"], key="dup_udef")

        st.write("")
        if st.button("➕ Salva Nuova Variante", type="primary"):
            if db_n and " (Personalizzato)" not in db_n:
                with st.spinner("Salvataggio in Cloud..."):
                    success = salva_su_cloud(db_n, db_cal, db_p, db_c, db_f, db_sat, db_fib, db_var, db_peso_pz, db_unita_def)
                    if success: 
                        st.session_state.msg_dup_ok = f"✅ Variante '{db_n}' salvata correttamente!"
                        st.session_state.do_clear_dup = True
                        st.rerun()
            elif " (Personalizzato)" in db_n:
                st.error("⚠️ Rinomina il prodotto eliminando la scritta '(Personalizzato)' prima di salvare.")
            else: 
                st.warning("Inserisci il nome della variante.")        

# ==========================================
# 👤 PAGINA 4: PROFILO E OBIETTIVI
# ==========================================
elif pagina_corrente == "👤 Profilo e Obiettivi":
    
    st.title("👤 Profilo e Obiettivi Nutrizionali")
    st.markdown("#### *Calcola il tuo fabbisogno e genera i tuoi target in automatico.* 🎯")
    st.write("")

    try:
        df_prof = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="Profilo", ttl=600)
        expected_cols = ["User_ID", "Peso", "Altezza", "Eta", "Sesso", "Attivita", "TGT_Cal", "TGT_C", "TGT_P", "TGT_F"]
        for c in expected_cols:
            if c not in df_prof.columns: df_prof[c] = None
    except Exception:
        df_prof = pd.DataFrame(columns=["User_ID", "Peso", "Altezza", "Eta", "Sesso", "Attivita", "TGT_Cal", "TGT_C", "TGT_P", "TGT_F"])

    u_prof = df_prof[df_prof['User_ID'] == USER_ID]
    
    # Valori salvati o default
    def_peso = float(u_prof.iloc[0]['Peso']) if not u_prof.empty and pd.notna(u_prof.iloc[0]['Peso']) else 75.0
    def_alt = int(u_prof.iloc[0]['Altezza']) if not u_prof.empty and pd.notna(u_prof.iloc[0]['Altezza']) else 175
    def_eta = int(u_prof.iloc[0]['Eta']) if not u_prof.empty and pd.notna(u_prof.iloc[0]['Eta']) else 52
    def_sesso = str(u_prof.iloc[0]['Sesso']) if not u_prof.empty and pd.notna(u_prof.iloc[0]['Sesso']) else "Uomo"
    def_att = str(u_prof.iloc[0]['Attivita']) if not u_prof.empty and pd.notna(u_prof.iloc[0]['Attivita']) else "Moderatamente Attivo (1.55) - Sport moderato 3-5 volte a sett"

    st.markdown("### 1️⃣ I tuoi Dati Personali")
    c1, c2, c3, c4 = st.columns(4)
    peso = c1.number_input("Peso attuale (kg)", min_value=30.0, max_value=200.0, value=def_peso, step=0.1)
    alt = c2.number_input("Altezza (cm)", min_value=100, max_value=250, value=def_alt, step=1)
    eta = c3.number_input("Età", min_value=10, max_value=100, value=def_eta, step=1)
    sesso = c4.selectbox("Sesso", ["Uomo", "Donna"], index=0 if def_sesso=="Uomo" else 1)
    
    attivita_list = {
        "Sedentario (1.2) - Lavoro da scrivania, no sport": 1.2,
        "Leggermente Attivo (1.375) - Sport leggero 1-3 volte a sett": 1.375,
        "Moderatamente Attivo (1.55) - Sport moderato 3-5 volte a sett": 1.55,
        "Molto Attivo (1.725) - Sport intenso 6-7 giorni": 1.725,
        "Extra Attivo (1.9) - Atleta agonista o lavoro fisico pesante": 1.9
    }
    idx_att = list(attivita_list.keys()).index(def_att) if def_att in attivita_list else 2
    att = st.selectbox("Livello di Attività Media", list(attivita_list.keys()), index=idx_att)
    
    # CALCOLO TDEE IN TEMPO REALE
    s = 5 if sesso == "Uomo" else -161
    bmr = (10 * peso) + (6.25 * alt) - (5 * eta) + s
    tdee = bmr * attivita_list[att]
    
    st.info(f"🧬 **Metabolismo Basale (BMR):** {bmr:.0f} kcal  |  🔥 **Dispendio Energetico Totale (TDEE):** {tdee:.0f} kcal")
    
    st.divider()

    st.markdown("### 2️⃣ Generazione Automatica dei Macros")
    st.write("Scegli il tuo obiettivo e imposta i fattori nutrizionali. I Carboidrati verranno calcolati automaticamente per coprire le calorie rimanenti.")
    
    col_ob1, col_ob2, col_ob3 = st.columns(3)
    
    obiettivo = col_ob1.selectbox(
        "Qual è il tuo obiettivo?", 
        ["Mantenimento (TDEE esatto)", "Dimagrimento Lieve (-300 kcal)", "Dimagrimento Marcato (-500 kcal)", "Costruzione Muscolare (+300 kcal)"]
    )
    
    if "Mantenimento" in obiettivo: tgt_cal_auto = tdee
    elif "Lieve" in obiettivo: tgt_cal_auto = tdee - 300
    elif "Marcato" in obiettivo: tgt_cal_auto = tdee - 500
    else: tgt_cal_auto = tdee + 300
    
    molt_p = col_ob2.slider("Fattore Proteine (g per kg di peso)", min_value=1.0, max_value=3.0, value=2.0, step=0.1, help="Per sportivi che si allenano coi pesi si consiglia 1.6 - 2.2 g/kg.")
    molt_f = col_ob3.slider("Fattore Grassi (g per kg di peso)", min_value=0.5, max_value=1.5, value=0.8, step=0.1, help="Per la salute ormonale il minimo sindacale è 0.5 g/kg. Media consigliata 0.8 - 1.0 g/kg.")

    # Calcolo esatto dei grammi
    calc_p = peso * molt_p
    calc_f = peso * molt_f
    
    # Calcolo calorie occupate da Pro e Grassi
    cal_occupate = (calc_p * 4) + (calc_f * 9)
    
    # I carboidrati sono tutto ciò che resta (se avanza spazio, altrimenti 0)
    calc_c = (tgt_cal_auto - cal_occupate) / 4 if tgt_cal_auto > cal_occupate else 0.0

    # CALCOLO E VISUALIZZAZIONE DELLE PERCENTUALI
    perc_p = ((calc_p * 4) / tgt_cal_auto) * 100 if tgt_cal_auto > 0 else 0
    perc_f = ((calc_f * 9) / tgt_cal_auto) * 100 if tgt_cal_auto > 0 else 0
    perc_c = ((calc_c * 4) / tgt_cal_auto) * 100 if tgt_cal_auto > 0 else 0

    st.markdown("#### 📊 Ripartizione Macros")
    st.success(f"🍞 **Carboidrati:** {perc_c:.0f}%  |  🥩 **Proteine:** {perc_p:.0f}%  |  🥑 **Grassi:** {perc_f:.0f}%")

    st.markdown("#### 🎯 I tuoi Target Finali da Salvare")
    st.write("Questi sono i valori generati. Se vuoi, puoi arrotondarli o ritoccarli a mano prima di salvare.")
    
    tc1, tc2, tc3, tc4 = st.columns(4)
    t_cal = tc1.number_input("Target Calorie", value=float(tgt_cal_auto), step=50.0)
    t_c = tc2.number_input("Target Carboidrati (g)", value=float(calc_c), step=5.0)
    t_p = tc3.number_input("Target Proteine (g)", value=float(calc_p), step=5.0)
    t_f = tc4.number_input("Target Grassi (g)", value=float(calc_f), step=5.0)

    cal_check = (t_c * 4) + (t_p * 4) + (t_f * 9)
    if abs(cal_check - t_cal) > 50:
        st.warning(f"⚠️ Nota matematica: I macro inseriti a mano generano circa {cal_check:.0f} kcal, ma il target in alto è {t_cal:.0f}. Non combaciano perfettamente.")
        
    st.write("")
    if st.button("💾 Conferma e Salva Obiettivi", type="primary", use_container_width=True):
        with st.spinner("Salvataggio in Cloud..."):
            try:
                df_prof = df_prof.dropna(subset=['User_ID'])
                df_prof_upd = df_prof[df_prof['User_ID'] != USER_ID]
                nuova_riga = pd.DataFrame({
                    "User_ID": [USER_ID], "Peso": [peso], "Altezza": [alt], "Eta": [eta], "Sesso": [sesso], 
                    "Attivita": [att], "TGT_Cal": [t_cal], "TGT_C": [t_c], "TGT_P": [t_p], "TGT_F": [t_f]
                })
                df_prof_upd = pd.concat([df_prof_upd, nuova_riga], ignore_index=True)
                conn.update(spreadsheet=SPREADSHEET_URL, worksheet="Profilo", data=df_prof_upd)
                st.cache_data.clear()
                st.success("✅ Profilo e Obiettivi aggiornati! Vai nel Diario Alimentare per vedere le Barre di Progresso colorate in azione.")
            except Exception as e:
                st.error(f"Errore di salvataggio. Assicurati di aver creato il foglio 'Profilo' in Google Sheets. Errore: {e}")

st.markdown("<br><br><div style='text-align: center; color: gray;'><small>⚡ Powerd by iannovins</small></div>", unsafe_allow_html=True)
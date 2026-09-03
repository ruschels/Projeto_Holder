import streamlit as st
import os
import time
import json
import uuid
import glob
import pandas as pd
import plotly.express as px
from datetime import datetime
from pypdf import PdfReader
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from google import genai
from google.genai import types
import yfinance as yf

# ==========================================
# CONFIGURAÇÕES DE SEGURANÇA E CHAVES
# ==========================================

def carregar_chave_api():
    """Verifica o arquivo JSON local primeiro; se não achar (nuvem), usa o st.secrets"""
    if os.path.exists("secrets.json"):
        try:
            with open("secrets.json", "r", encoding="utf-8") as f:
                secrets_locais = json.load(f)
                return secrets_locais.get("API_GEMINI", "CHAVE_NAO_ENCONTRADA")
        except Exception:
            return "CHAVE_NAO_ENCONTRADA"
            
    try:
        if "API_GEMINI" in st.secrets:
            return st.secrets["API_GEMINI"]
    except Exception:
        pass
        
    return "CHAVE_NAO_ENCONTRADA"

API_GEMINI = carregar_chave_api()
MODELO_GEMINI = "gemini-flash-latest"

# ==========================================
# CONFIGURAÇÕES DA PÁGINA (DESIGN NATIVO)
# ==========================================
st.set_page_config(
    page_title="Holder System #PAS", 
    layout="wide", 
    page_icon="🐶",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# ESTRUTURA DE DIRETÓRIOS E ARQUIVOS JSON
# ==========================================

PASTAS = ["pdfs_balancos", "pdfs_filosofia", "Legenda", "Historico"]
for p in PASTAS:
    os.makedirs(p, exist_ok=True)

ARQUIVOS_JSON = {
    "base_conhecimento_variavel.json": {},
    "conhecimento.json": {},
    "gastos.json": [],
    "ideias.json": [],
}

for json_file, default_content in ARQUIVOS_JSON.items():
    if not os.path.exists(json_file):
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, ensure_ascii=False, indent=4)

# Inicialização inteligente da carteira buscando o backup mais recente na pasta Historico
def inicializar_carteira():
    padrao_busca = os.path.join("Historico", "*.json")
    arquivos_historico = glob.glob(padrao_busca)
    
    if arquivos_historico:
        # Pega o arquivo mais recente com base na data de modificação
        arquivo_mais_recente = max(arquivos_historico, key=os.path.getmtime)
        try:
            with open(arquivo_mais_recente, "r", encoding="utf-8") as f:
                dados_recente = json.load(f)
                # Salva como o carteira.json ativo do sistema
                with open("carteira.json", "w", encoding="utf-8") as f_ativo:
                    json.dump(dados_recente, f_ativo, ensure_ascii=False, indent=4)
                return
        except Exception:
            pass

    # Fallback caso a pasta Historico esteja vazia
    if not os.path.exists("carteira.json"):
        default_carteira = {
            "rv_br": {},
            "rv_us": {},
            "rf_br": {},
            "btc": 0.0,
            "alvos_macro": {"rv_br": 45.0, "rv_us": 45.0, "rf_br": 0.0, "btc": 10.0},
            "alvos_ativos": {"rv_br": {}, "rv_us": {}}
        }
        with open("carteira.json", "w", encoding="utf-8") as f:
            json.dump(default_carteira, f, ensure_ascii=False, indent=4)

# Garante que o backup do Historico seja puxado apenas ao ligar o app, e não a cada clique
if 'carteira_carregada' not in st.session_state:
    inicializar_carteira()
    st.session_state.carteira_carregada = True

# ==========================================
# FUNÇÕES AUXILIARES E MOTOR DE IA
# ==========================================
def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    return "".join([page.extract_text() + "\n" for page in reader.pages])

def carregar_json(caminho):
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)

def salvar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)

def chamar_gemini_com_retry(prompt, forcar_json=False, status_container=None, max_tentativas_por_modelo=3, delay_segundos=4):
    modelos_fallback = [MODELO_GEMINI, "gemini-3.6-flash", "gemini-2.5-flash"]
    client = genai.Client(api_key=API_GEMINI)
    
    log_container = status_container if status_container else st.empty()
    config = types.GenerateContentConfig(response_mime_type="application/json") if forcar_json else None
    
    for modelo in modelos_fallback:
        for tentativa in range(max_tentativas_por_modelo):
            log_container.info(f"🔄 Tentando conectar ao modelo: **{modelo}**...")
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=config,
                )
                log_container.success(f"✅ Sucesso! O modelo **{modelo}** processou.")
                time.sleep(1)
                log_container.empty() 
                return response.text
                
            except Exception as e:
                erro_str = str(e)
                if "503" in erro_str or "429" in erro_str:
                    log_container.warning(f"⚠️ **{modelo}** sobrecarregado. Aguardando...")
                    if tentativa < max_tentativas_por_modelo - 1:
                        time.sleep(delay_segundos)
                        continue
                    else: 
                        break 
                else:
                    log_container.error(f"❌ Erro no **{modelo}**... Pulando.")
                    time.sleep(2) 
                    break 
                    
    raise Exception("Falha de conexão em todas as rotas. Tente novamente.")

def raspar_dados_statusinvest(ticker, base_variavel, log_container=None, is_etf_us=False):
    eh_br = any(char.isdigit() for char in ticker)
    
    if is_etf_us:
        url = f"https://statusinvest.com.br/etf/eua/{ticker.lower()}" 
    else:
        url = f"https://statusinvest.com.br/acoes/{ticker.lower()}" if eh_br else f"https://statusinvest.com.br/acoes/eua/{ticker.lower()}"
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    
    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.get(url)
        time.sleep(4)
        sopa = BeautifulSoup(driver.page_source, 'html.parser')
        for tag in sopa(["script", "style", "nav", "footer", "svg", "header"]):
            tag.decompose()
        texto_limpo = sopa.get_text(separator=' ', strip=True)
        
        prompt = f"""
        Você é um analista fundamentalista focado em Buy and Hold e #PAS. 
        Analise o texto do StatusInvest para {ticker} e retorne ESTRITAMENTE um JSON com:
        - "nome_empresa": (string)
        - "setor": (string)
        - "tem_acao_ordinaria_ON": (boolean)
        - "lucros_consistentes": (boolean)
        - "divida_equilibrada": (boolean)
        - "resumo_numeros": (string)
        - "pontos_de_atencao": (string)
        - "observacao": (string - vazio por padrão)
        TEXTO: {texto_limpo[:60000]}
        """
        resultado = chamar_gemini_com_retry(prompt, forcar_json=True, status_container=log_container)
        dados = json.loads(resultado)
        if ticker in base_variavel and "observacao" in base_variavel[ticker]:
            dados["observacao"] = base_variavel[ticker]["observacao"]
        base_variavel[ticker] = dados
        salvar_json("base_conhecimento_variavel.json", base_variavel)
        return True
    except Exception as e:
        if log_container:
            log_container.error(f"Erro ao raspar {ticker}: {e}")
        return False
    finally:
        driver.quit()

@st.cache_data(ttl=300)
def obter_cotacao(ticker, is_br=True):
    try:
        simbolo = f"{ticker}.SA" if is_br else ticker
        ticker_obj = yf.Ticker(simbolo)
        return ticker_obj.fast_info['lastPrice']
    except:
        return 0.0

@st.cache_data(ttl=3600)
def obter_cambio_usd_brl():
    try:
        return yf.Ticker("USDBRL=X").fast_info['lastPrice']
    except:
        return 5.50

# ==========================================
# MENU LATERAL LIMPO
# ==========================================
with st.sidebar:
    st.title("🐶 Holder System")
    st.caption("Gestão Patrimonial, Produtividade e #PAS")
    st.divider()
    
    menu = st.radio("Navegação", [
        "📊 1. Ingestão de Balanços", 
        "📚 2. Base da Filosofia",
        "🤖 3. Consultar Agente",
        "📝 4. Transcrições YouTube",
        "🏢 5. Visualizar Empresas",
        "🌐 6. Raspagem StatusInvest",
        "🐷 7. PoupaMês (Finanças)",
        "💡 8. Ideias de Conteúdo",
        "💼 9. Minha Carteira"
    ])

# ==========================================
# ABA 1: INGESTÃO DE BALANÇOS
# ==========================================
if menu == "📊 1. Ingestão de Balanços":
    st.header("📊 Leitor Automático de Resultados")
    st.write("Faça o upload do Release de Resultados ou DFP (PDF) da empresa para extração fundamentalista.")
    
    with st.container(border=True):
        col1, col2 = st.columns([1, 2])
        with col1:
            ticker = st.text_input("Ticker da Empresa", placeholder="Ex: WEGE3").upper()
        with col2:
            uploaded_file = st.file_uploader("Arquivo PDF do Balanço", type="pdf")
            
        if st.button("🚀 Processar Balanço", type="primary"):
            if uploaded_file and ticker:
                with st.spinner("Lendo documento financeiro..."):
                    texto_pdf = extract_text_from_pdf(uploaded_file)
                    container_log = st.empty()
                    prompt = f"Analise o balanço de {ticker} e retorne um JSON com: nome_empresa, setor, tem_acao_ordinaria_ON, lucros_consistentes, divida_equilibrada, resumo_numeros, pontos_de_atencao. Texto: {texto_pdf[:80000]}"
                    try:
                        res = chamar_gemini_com_retry(prompt, forcar_json=True, status_container=container_log)
                        dados = json.loads(res)
                        base = carregar_json("base_conhecimento_variavel.json")
                        if ticker in base and "observacao" in base[ticker]:
                            dados["observacao"] = base[ticker]["observacao"]
                        base[ticker] = dados
                        salvar_json("base_conhecimento_variavel.json", base)
                        st.success(f"✅ Dados de {ticker} salvos com sucesso!")
                        st.json(dados)
                    except Exception as e:
                        st.error(f"Erro: {e}")
            else:
                st.warning("Preencha o Ticker e envie o arquivo PDF.")

# ==========================================
# ABA 2: BASE DA FILOSOFIA
# ==========================================
elif menu == "📚 2. Base da Filosofia":
    st.header("📚 Cérebro e Base Filosófica")
    st.write("Alimente o agente com livros, artigos e regras de investimento.")
    
    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            autor_topico = st.text_input("Tema / Autor", placeholder="Ex: Bastter - Regras Iniciais")
        with col2:
            uploaded_filo = st.file_uploader("PDF de Filosofia", type="pdf")
            
        if st.button("📥 Absorver Conhecimento", type="primary") and uploaded_filo and autor_topico:
            with st.spinner("Processando material..."):
                texto = extract_text_from_pdf(uploaded_filo)
                base = carregar_json("conhecimento.json")
                base[autor_topico] = texto[:50000]
                salvar_json("conhecimento.json", base)
                st.success("✅ Filosofia integrada ao Agente!")

    st.subheader("📖 Tópicos Já Catalogados")
    base_atual = carregar_json("conhecimento.json")
    if base_atual:
        for chave in base_atual.keys():
            st.markdown(f"- 📌 **{chave}**")
    else:
        st.info("Nenhuma filosofia cadastrada.")

# ==========================================
# ABA 3: CONSULTAR AGENTE
# ==========================================
elif menu == "🤖 3. Consultar Agente":
    st.header("🐶 Consultoria com o Agente Holder")
    
    with st.container(border=True):
        modo = st.radio("Selecione o modo de interação:", [
            "Dar a Voadora (Analisar Empresa)", 
            "Receber Conhecimento (#PAS)",
            "Tirar Dúvida (Perguntar)"
        ], horizontal=True)
    
    base_var = carregar_json("base_conhecimento_variavel.json")
    base_filo = " ".join(carregar_json("conhecimento.json").values())
    
    if modo == "Dar a Voadora (Analisar Empresa)":
        if base_var:
            empresa = st.selectbox("Escolha a empresa:", list(base_var.keys()))
            if st.button("💥 Dar a Voadora", type="primary"):
                prompt = f"Você é o Bastter. Analise com rigor e valor {empresa} com base nestes dados: {json.dumps(base_var[empresa])} e filosofia: {base_filo[:8000]}"
                with st.spinner("O agente está formulando a análise..."):
                    resp = chamar_gemini_com_retry(prompt)
                    st.info(resp)
        else:
            st.info("Cadastre empresas na aba de raspagem ou balanços primeiro.")
            
    elif modo == "Receber Conhecimento (#PAS)":
        if st.button("🧠 Gerar Pílulas de Conhecimento", type="primary"):
            prompt = f"Gere pílulas de conhecimento curtas, diretas e sem rodeios sobre Buy and Hold e paz de espírito com base nisso: {base_filo[:10000]}"
            with st.spinner("Buscando ensinamentos..."):
                resp = chamar_gemini_com_retry(prompt)
                st.markdown(f"> {resp}")
                
    else:
        pergunta = st.text_input("Qual é a sua dúvida?", placeholder="Ex: Com base na minha carteira alvo, onde devo aportar hoje?")
        
        if st.button("Perguntar ao Agente", type="primary") and pergunta:
            
            dados_carteira = carregar_json("carteira.json")
            dados_empresas = carregar_json("base_conhecimento_variavel.json")
            dados_gastos = carregar_json("gastos.json")
            
            contexto_sistema = f"""
            ESTADO ATUAL DO SISTEMA DO USUÁRIO:
            
            [CARTEIRA ALVO E POSIÇÕES ATUAIS]
            {json.dumps(dados_carteira, ensure_ascii=False)}
            
            [EMPRESAS MONITORADAS E FUNDAMENTOS]
            {json.dumps(dados_empresas, ensure_ascii=False)}
            
            [CONTROLE DE GASTOS (POUPAMÊS)]
            {json.dumps(dados_gastos, ensure_ascii=False)}
            """
            
            prompt = f"""
            Você é o Cérebro Holder, um consultor financeiro brutalmente honesto, focado em Buy and Hold e paz de espírito (#PAS).
            
            REGRAS DE CONDUTA:
            - Baseie-se ESTRITAMENTE na filosofia abaixo.
            - Analise o ESTADO ATUAL DO SISTEMA para dar uma resposta 100% personalizada e matemática.
            - Se o usuário perguntar onde aportar, olhe a carteira alvo dele, veja o que está mais para trás e recomende o aporte naquilo, desde que a empresa tenha lucros consistentes na base de fundamentos.
            
            FILOSOFIA BASE: 
            {base_filo[:10000]}
            
            {contexto_sistema}
            
            DÚVIDA DO USUÁRIO: 
            {pergunta}
            """
            
            with st.spinner("Analisando seus balanços, sua carteira e consultando a filosofia..."):
                resp = chamar_gemini_com_retry(prompt)
                st.info(resp)

# ==========================================
# ABA 4: TRANSCRIÇÕES YOUTUBE
# ==========================================
elif menu == "📝 4. Transcrições YouTube":
    st.header("📝 Aprendizado via Legendas de Vídeos")
    leg_files = [f for f in os.listdir("Legenda") if f.endswith(".txt")]
    
    if leg_files:
        with st.container(border=True):
            sel_leg = st.selectbox("Selecione a transcrição:", leg_files)
            if st.button("🧠 Extrair Conhecimento", type="primary"):
                with open(os.path.join("Legenda", sel_leg), "r", encoding="utf-8") as f:
                    texto_leg = f.read()
                prompt = f"Extraia filosofia e insights de empresas desta transcrição em JSON (titulo_tema, resumo_filosofia, insights_empresas): {texto_leg[:60000]}"
                try:
                    res = chamar_gemini_com_retry(prompt, forcar_json=True)
                    dados = json.loads(res)
                    if dados.get("titulo_tema"):
                        bc = carregar_json("conhecimento.json")
                        bc[dados["titulo_tema"]] = dados["resumo_filosofia"]
                        salvar_json("conhecimento.json", bc)
                        st.success(f"✅ Conhecimento salvo: {dados['titulo_tema']}")
                except Exception as e:
                    st.error(f"Erro: {e}")
    else:
        st.info("A pasta 'Legenda' está vazia.")

# ==========================================
# ABA 5: VISUALIZAR EMPRESAS
# ==========================================
elif menu == "🏢 5. Visualizar Empresas":
    st.header("🏢 Diretório de Empresas Cadastradas")
    base_var = carregar_json("base_conhecimento_variavel.json")

    with st.expander("💾 Backup & Restauração de Empresas", expanded=False):
        st.write("Salve uma cópia de segurança da sua base de empresas raspadas ou faça o upload de um arquivo local para a nuvem.")
        col_b1, col_b2 = st.columns(2, gap="large")
        
        with col_b1:
            st.subheader("⬇️ Exportar Backup")
            json_str_empresas = json.dumps(base_var, ensure_ascii=False, indent=4)
            data_atual = datetime.now().strftime('%Y%m%d')
            st.download_button(
                label="Baixar base_conhecimento_variavel.json",
                data=json_str_empresas,
                file_name=f"backup_empresas_{data_atual}.json",
                mime="application/json",
                use_container_width=True
            )
            
        with col_b2:
            st.subheader("⬆️ Importar Backup")
            arquivo_upload_empresas = st.file_uploader("Selecione o arquivo .json", type=["json"], key="upload_empresas", label_visibility="collapsed")
            if st.button("Restaurar Base de Empresas", type="primary", use_container_width=True):
                if arquivo_upload_empresas is not None:
                    sucesso_emp = False
                    try:
                        conteudo_emp = arquivo_upload_empresas.getvalue().decode("utf-8")
                        dados_restaurados_emp = json.loads(conteudo_emp)
                        
                        if isinstance(dados_restaurados_emp, dict):
                            salvar_json("base_conhecimento_variavel.json", dados_restaurados_emp)
                            sucesso_emp = True
                        else:
                            st.error("❌ Arquivo inválido. Certifique-se de usar um backup da base de empresas.")
                    except:
                        st.error("❌ Erro ao ler o arquivo. Formato corrompido.")
                        
                    if sucesso_emp:
                        st.success("✅ Base de empresas restaurada com sucesso! Atualizando...")
                        time.sleep(1.5)
                        st.rerun()

    st.divider()
    
    if base_var:
        def boa(d): return d.get('lucros_consistentes', False) and d.get('divida_equilibrada', False)
        br, us = [], []
        for t, d in base_var.items():
            (br if any(c.isdigit() for c in t) else us).append((t, d))
        
        br.sort(key=lambda x: 0 if boa(x[1]) else 1)
        us.sort(key=lambda x: 0 if boa(x[1]) else 1)
        
        col1, col2 = st.columns(2)
        
        def render_lista(lst, col):
            with col:
                for t, d in lst:
                    ok = boa(d)
                    with st.expander(f"{'🟢' if ok else '🔴'} {t} — {d.get('nome_empresa', t)}"):
                        st.markdown(f"**Setor:** {d.get('setor', 'N/A')}")
                        st.markdown(f"**Resumo:** {d.get('resumo_numeros', 'N/A')}")
                        if d.get("observacao"):
                            st.info(d["observacao"])
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("🔄 Atualizar", key=f"up_{t}", use_container_width=True):
                                container = st.empty()
                                raspar_dados_statusinvest(t, base_var, container)
                                st.rerun()
                        with c2:
                            if st.button("🗑️ Excluir", key=f"del_{t}", use_container_width=True):
                                del base_var[t]
                                salvar_json("base_conhecimento_variavel.json", base_var)
                                st.rerun()

        with col1:
            st.subheader("🇧🇷 Brasil")
        with col2:
            st.subheader("🇺🇸 Exterior")

        render_lista(br, col1)
        render_lista(us, col2)
    else:
        st.info("Nenhuma empresa cadastrada no sistema.")

# ==========================================
# ABA 6: RASPAGEM STATUSINVEST
# ==========================================
elif menu == "🌐 6. Raspagem StatusInvest":
    st.header("🌐 Raspagem de Fundamentos em Massa")
    
    with st.container(border=True):
        st.write("Extraia automaticamente os lucros e dívidas direto do site StatusInvest.")
        tickers = st.text_input("Tickers separados por vírgula", placeholder="Ex: WEGE3, AAPL, ITUB3")
        
        is_etf = st.checkbox("Marcador de ETF", help="Marque esta opção se os tickers inseridos acima forem ETFs do mercado americano (ex: AVUV, AVLV).")
        
        if st.button("🚀 Iniciar Raspagem", type="primary") and tickers:
            lista = [t.strip().upper() for t in tickers.split(",") if t.strip()]
            prog = st.progress(0)
            log = st.empty()
            base = carregar_json("base_conhecimento_variavel.json")
            for i, t in enumerate(lista):
                log.info(f"Raspando {t}...")
                
                raspar_dados_statusinvest(t, base, log, is_etf_us=is_etf)
                
                prog.progress((i + 1) / len(lista))
            log.success("🎉 Raspagem concluída! Verifique as empresas na Aba 5.")

# ==========================================
# ABA 7: POUPAMÊS (FINANÇAS)
# ==========================================
elif menu == "🐷 7. PoupaMês (Finanças)":
    st.header("🐷 PoupaMês - Controle de Gastos")
    
    DATA_FILE = "gastos.json"
    CATEGORIES = {
        "Receita": ["Salário", "Freelance/Renda Extra", "Rendimentos", "Outros"],
        "Despesa": ["Moradia", "Alimentação", "Transporte", "Saúde", "Lazer", "Educação", "Compras", "Outros"]
    }
    
    if 'transactions' not in st.session_state:
        st.session_state.transactions = carregar_json(DATA_FILE)
        
    txs = st.session_state.transactions
    inc = sum(t['amount'] for t in txs if t['type'] == 'Receita')
    exp = sum(t['amount'] for t in txs if t['type'] == 'Despesa')
    bal = inc - exp
    pct = max(0, (bal / inc) * 100) if inc > 0 else 0
    
    st.subheader(f"Progresso de Poupança: {pct:.0f}%")
    st.progress(min(int(pct) / 100.0, 1.0))
    
    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            st.metric("Receitas", f"R$ {inc:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    with c2:
        with st.container(border=True):
            st.metric("Despesas", f"R$ {exp:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    with c3:
        with st.container(border=True):
            st.metric("Saldo Atual", f"R$ {bal:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    
    st.divider()
    
    col_l1, col_l2 = st.columns([1, 1.5], gap="large")
    
    with col_l1:
        with st.container(border=True):
            st.subheader("➕ Novo Lançamento")
            t_type = st.radio("Tipo", ["Despesa", "Receita"], horizontal=True)
            t_desc = st.text_input("Descrição", placeholder="Ex: Supermercado")
            t_amount = st.number_input("Valor (R$)", min_value=0.01, step=10.0, format="%.2f")
            t_cat = st.selectbox("Categoria", CATEGORIES[t_type])
            
            if st.button("Salvar Lançamento", type="primary", use_container_width=True):
                if t_desc and t_amount > 0:
                    new_tx = {
                        "id": str(uuid.uuid4()),
                        "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "type": t_type, "description": t_desc,
                        "category": t_cat, "amount": float(t_amount)
                    }
                    st.session_state.transactions.append(new_tx)
                    salvar_json(DATA_FILE, st.session_state.transactions)
                    st.success("Salvo com sucesso!")
                    st.rerun()
                
    with col_l2:
        with st.container(border=True):
            st.subheader("📊 Distribuição")
            exp_data = [t for t in txs if t['type'] == 'Despesa']
            df_g = pd.DataFrame(exp_data) if exp_data else pd.DataFrame(columns=['category', 'amount'])
            if bal > 0:
                df_g = pd.concat([df_g, pd.DataFrame([{'category': 'Poupado', 'amount': bal}])], ignore_index=True)
                
            if not df_g.empty and 'category' in df_g.columns:
                df_grouped = df_g.groupby('category')['amount'].sum().reset_index()
                fig = px.pie(df_grouped, values='amount', names='category', hole=0.65)
                fig.update_layout(margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Registre despesas para ver os gráficos.")
            
    st.divider()
    st.subheader("📋 Histórico Recente")
    if txs:
        df_all = pd.DataFrame(txs)
        df_display = df_all[['date', 'description', 'category', 'type', 'amount']].iloc[::-1].copy()
        df_display['amount'] = df_display['amount'].apply(lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        df_display.columns = ["Data", "Descrição", "Categoria", "Tipo", "Valor"]
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        if st.button("🗑️ Limpar Todos os Registros"):
            st.session_state.transactions = []
            salvar_json(DATA_FILE, [])
            st.rerun()

# ==========================================
# ABA 8: IDEIAS DE CONTEÚDO
# ==========================================
elif menu == "💡 8. Ideias de Conteúdo":
    st.header("💡 Banco de Ideias para Vídeos")
    st.write("Anote seus rascunhos e insights de produção de conteúdo aqui.")
    
    IDEIAS_FILE = "ideias.json"
    if 'lista_ideias' not in st.session_state:
        st.session_state.lista_ideias = carregar_json(IDEIAS_FILE)
        
    ideias = st.session_state.lista_ideias

    col1, col2 = st.columns([1, 2], gap="large")

    with col1:
        with st.container(border=True):
            st.subheader("Nova Ideia")
            titulo_ideia = st.text_input("Título / Tema", placeholder="Ex: 3 defesas essenciais da montada")
            roteiro_ideia = st.text_area("Rascunho / Tópicos", placeholder="Ex: 1. Postura firme\n2. Bloqueio de quadril\n3. Reposição", height=150)
            
            if st.button("Salvar Ideia", type="primary", use_container_width=True):
                if titulo_ideia:
                    nova_ideia = {
                        "id": str(uuid.uuid4()),
                        "data": datetime.now().strftime("%d/%m/%Y"),
                        "titulo": titulo_ideia,
                        "roteiro": roteiro_ideia
                    }
                    st.session_state.lista_ideias.append(nova_ideia)
                    salvar_json(IDEIAS_FILE, st.session_state.lista_ideias)
                    st.success("Ideia guardada no banco!")
                    st.rerun()
                else:
                    st.warning("Preencha ao menos o título.")

    with col2:
        st.subheader("📝 Rascunhos Salvos")
        if ideias:
            for ideia in reversed(ideias):
                with st.container(border=True):
                    st.markdown(f"**{ideia['titulo']}**  *(Criado em {ideia['data']})*")
                    if ideia['roteiro']:
                        st.info(ideia['roteiro'])
                    
                    if st.button("🗑️ Apagar", key=f"del_ideia_{ideia['id']}"):
                        st.session_state.lista_ideias = [i for i in st.session_state.lista_ideias if i['id'] != ideia['id']]
                        salvar_json(IDEIAS_FILE, st.session_state.lista_ideias)
                        st.rerun()
        else:
            st.info("Sua mente está vazia por enquanto. Adicione uma ideia ao lado.")

# ==========================================
# ABA 9: MINHA CARTEIRA (#PAS)
# ==========================================
elif menu == "💼 9. Minha Carteira":
    st.header("💼 Minha Carteira Alvo (#PAS)")
    
    def atualizar_quantidade(classe, ticker, key_widget):
        nova_qtd = st.session_state[key_widget]
        carteira_temp = carregar_json("carteira.json")
        carteira_temp[classe][ticker] = nova_qtd
        salvar_json("carteira.json", carteira_temp)
    
    carteira = carregar_json("carteira.json")
    base_var = carregar_json("base_conhecimento_variavel.json")
    
    if "alvos_macro" not in carteira:
        carteira["alvos_macro"] = {"rv_br": 45.0, "rv_us": 45.0, "rf_br": 0.0, "btc": 10.0}
    if "alvos_ativos" not in carteira:
        carteira["alvos_ativos"] = {"rv_br": {}, "rv_us": {}}
    
    opcoes_br = [t for t in base_var.keys() if any(c.isdigit() for c in t)]
    opcoes_us = [t for t in base_var.keys() if not any(c.isdigit() for c in t)]
    
    with st.expander("🎯 Configurar Alvos Macro (Grandes Áreas)", expanded=False):
        st.write("Defina o percentual ideal de cada classe. A soma deve ser exatamente 100%.")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        
        with col_m1:
            novo_rv_br = st.number_input("Renda Variável BR (%)", value=carteira["alvos_macro"]["rv_br"], min_value=0.0, max_value=100.0, step=1.0)
        with col_m2:
            novo_rv_us = st.number_input("Exterior (%)", value=carteira["alvos_macro"]["rv_us"], min_value=0.0, max_value=100.0, step=1.0)
        with col_m3:
            novo_rf_br = st.number_input("Renda Fixa (%)", value=carteira["alvos_macro"]["rf_br"], min_value=0.0, max_value=100.0, step=1.0)
        with col_m4:
            novo_btc = st.number_input("Bitcoin (%)", value=carteira["alvos_macro"]["btc"], min_value=0.0, max_value=100.0, step=1.0)
            
        soma_macro = novo_rv_br + novo_rv_us + novo_rf_br + novo_btc
        
        if soma_macro != 100.0:
            st.error(f"⚠️ A soma total está em **{soma_macro}%**. Ajuste os valores para somar exatamente 100%.")
        else:
            st.success("✅ Alocação perfeitamente balanceada em 100%.")
            if st.button("Salvar Alvos Macro", type="primary"):
                carteira["alvos_macro"] = {"rv_br": novo_rv_br, "rv_us": novo_rv_us, "rf_br": novo_rf_br, "btc": novo_btc}
                salvar_json("carteira.json", carteira)
                st.rerun()

    with st.expander("➕ Cadastrar Novo Ativo ou Ajustar Alvo Interno", expanded=False):
        c_tipo, c_ativo, c_qtd, c_alvo, c_btn = st.columns([2, 2, 1.5, 1.5, 1])
        
        with c_tipo:
            tipo_alocacao = st.selectbox("Classe", ["Renda Variável BR", "Exterior", "Renda Fixa BR", "Bitcoin"], key="tipo_cad")
        
        with c_ativo:
            if tipo_alocacao == "Renda Variável BR":
                ativo_selecionado = st.selectbox("Empresa", opcoes_br if opcoes_br else ["Nenhuma empresa BR cadastrada"], key="ativo_cad_br")
            elif tipo_alocacao == "Exterior":
                ativo_selecionado = st.selectbox("Empresa", opcoes_us if opcoes_us else ["Nenhuma empresa US cadastrada"], key="ativo_cad_us")
            elif tipo_alocacao == "Renda Fixa BR":
                ativo_selecionado = st.text_input("Nome do Título", placeholder="Ex: IPCA+ 2035", key="ativo_cad_rf")
            else:
                ativo_selecionado = st.text_input("Ativo", value="BTC", disabled=True, key="ativo_cad_btc")
                
        with c_qtd:
            qtd_inserida = st.number_input("Qtd Inicial", min_value=0.0, step=0.0001, format="%0.4f", key="qtd_cad")
            
        with c_alvo:
            if tipo_alocacao in ["Renda Variável BR", "Exterior"]:
                alvo_interno = st.number_input("Alvo Interno (%)", min_value=0.0, max_value=100.0, step=1.0, key="alvo_cad")
            else:
                alvo_interno = 0.0
                st.write("") 
                
        with c_btn:
            st.write("") 
            st.write("")
            if st.button("Salvar Cadastro"):
                if ativo_selecionado and not ativo_selecionado.startswith("Nenhuma"):
                    if tipo_alocacao == "Renda Variável BR":
                        carteira["rv_br"][ativo_selecionado] = qtd_inserida
                        carteira["alvos_ativos"]["rv_br"][ativo_selecionado] = alvo_interno
                    elif tipo_alocacao == "Exterior":
                        carteira["rv_us"][ativo_selecionado] = qtd_inserida
                        carteira["alvos_ativos"]["rv_us"][ativo_selecionado] = alvo_interno
                    elif tipo_alocacao == "Renda Fixa BR":
                        carteira["rf_br"][ativo_selecionado] = qtd_inserida
                    elif tipo_alocacao == "Bitcoin":
                        carteira["btc"] = qtd_inserida
                    
                    salvar_json("carteira.json", carteira)
                    st.success("Salvo!")
                    time.sleep(0.5)
                    st.rerun()

    with st.expander("💸 Fazer Aporte (Registrar Compra)", expanded=False):
        st.write("Acabou de comprar? Registre aqui a quantidade física de ativos adquiridos para somar à sua carteira.")
        a_tipo, a_ativo, a_qtd, a_btn = st.columns([2, 2, 1.5, 1.5])
        
        with a_tipo:
            tipo_aporte = st.selectbox("Classe do Aporte", ["Renda Variável BR", "Exterior", "Renda Fixa BR", "Bitcoin"], key="tipo_apo")
            
        with a_ativo:
            if tipo_aporte == "Renda Variável BR":
                ativos_disponiveis = list(carteira.get("rv_br", {}).keys())
                ativo_aporte = st.selectbox("Ativo (BR)", ativos_disponiveis if ativos_disponiveis else ["Nenhum ativo na carteira"], key="ativo_apo_br")
            elif tipo_aporte == "Exterior":
                ativos_disponiveis = list(carteira.get("rv_us", {}).keys())
                ativo_aporte = st.selectbox("Ativo (US)", ativos_disponiveis if ativos_disponiveis else ["Nenhum ativo na carteira"], key="ativo_apo_us")
            elif tipo_aporte == "Renda Fixa BR":
                ativos_disponiveis = list(carteira.get("rf_br", {}).keys())
                ativo_aporte = st.selectbox("Título RF", ativos_disponiveis if ativos_disponiveis else ["Nenhum ativo na carteira"], key="ativo_apo_rf")
            else:
                ativo_aporte = st.text_input("Cripto", value="BTC", disabled=True, key="ativo_apo_btc")
                
        with a_qtd:
            qtd_comprada = st.number_input("Quantidade Comprada", min_value=0.0, step=0.0001, format="%0.4f", help="Quantidade física.", key="qtd_apo")
            
        with a_btn:
            st.write("") 
            st.write("")
            if st.button("Confirmar Aporte", type="primary"):
                if ativo_aporte and not ativo_aporte.startswith("Nenhum") and qtd_comprada > 0:
                    if tipo_aporte == "Renda Variável BR":
                        carteira["rv_br"][ativo_aporte] = carteira["rv_br"].get(ativo_aporte, 0.0) + qtd_comprada
                    elif tipo_aporte == "Exterior":
                        carteira["rv_us"][ativo_aporte] = carteira["rv_us"].get(ativo_aporte, 0.0) + qtd_comprada
                    elif tipo_aporte == "Renda Fixa BR":
                        carteira["rf_br"][ativo_aporte] = carteira["rf_br"].get(ativo_aporte, 0.0) + qtd_comprada
                    elif tipo_aporte == "Bitcoin":
                        carteira["btc"] = carteira.get("btc", 0.0) + qtd_comprada
                    
                    salvar_json("carteira.json", carteira)
                    st.success(f"Aporte de {qtd_comprada} em {ativo_aporte} registrado!")
                    time.sleep(0.5)
                    st.rerun()
                elif qtd_comprada <= 0:
                    st.warning("Insira uma quantidade maior que zero.")

    with st.expander("💾 Backup & Restauração", expanded=False):
        st.write("Salve uma cópia de segurança de toda a sua configuração de carteira ou restaure um arquivo antigo.")
        col_b1, col_b2 = st.columns(2, gap="large")
        
        with col_b1:
            st.subheader("⬇️ Exportar Backup")
            json_str = json.dumps(carteira, ensure_ascii=False, indent=4)
            data_atual = datetime.now().strftime('%Y%m%d')
            st.download_button(
                label="Baixar carteira.json",
                data=json_str,
                file_name=f"backup_carteira_{data_atual}.json",
                mime="application/json",
                use_container_width=True
            )
            
        with col_b2:
            st.subheader("⬆️ Importar Backup")
            arquivo_upload = st.file_uploader("Selecione o arquivo .json", type=["json"], label_visibility="collapsed")
            if st.button("Restaurar Dados da Carteira", type="primary", use_container_width=True):
                if arquivo_upload is not None:
                    sucesso = False
                    try:
                        conteudo = arquivo_upload.getvalue().decode("utf-8")
                        dados_restaurados = json.loads(conteudo)
                        
                        if "alvos_macro" in dados_restaurados:
                            salvar_json("carteira.json", dados_restaurados)
                            sucesso = True
                        else:
                            st.error("❌ Arquivo inválido. Certifique-se de usar um backup do sistema.")
                    except:
                        st.error("❌ Erro ao ler o arquivo. Formato corrompido.")
                        
                    if sucesso:
                        st.success("✅ Backup restaurado com sucesso! Atualizando...")
                        time.sleep(1.5)
                        st.rerun()

    st.divider()

    usd_rate = obter_cambio_usd_brl()
    
    subtotal_rv_br = 0.0
    precos_br = {}
    for tick, qtd in carteira.get("rv_br", {}).items():
        p = obter_cotacao(tick, is_br=True)
        precos_br[tick] = p
        subtotal_rv_br += p * qtd
        
    subtotal_us_usd = 0.0
    precos_us = {}
    for tick, qtd in carteira.get("rv_us", {}).items():
        p = obter_cotacao(tick, is_br=False)
        precos_us[tick] = p
        subtotal_us_usd += p * qtd
    subtotal_us_brl = subtotal_us_usd * usd_rate
    
    subtotal_rf = sum(carteira.get("rf_br", {}).values())
    
    qtd_btc = carteira.get("btc", 0.0)
    try:
        preco_btc_usd = yf.Ticker("BTC-USD").fast_info['lastPrice']
        preco_btc = preco_btc_usd * usd_rate
    except:
        preco_btc = 0.0
    subtotal_btc = qtd_btc * preco_btc
    
    total_patrimonio = subtotal_rv_br + subtotal_us_brl + subtotal_rf + subtotal_btc

    st.markdown(f"<h2 style='text-align: center; color: #0284c7;'>Patrimônio Total: R$ {total_patrimonio:,.2f}</h2>", unsafe_allow_html=True)
    st.write("")
    
    df_alvo = pd.DataFrame({
        "Classe": ["Renda Variável BR", "Exterior", "Renda Fixa BR", "Bitcoin"],
        "Percentual": [carteira["alvos_macro"]["rv_br"], carteira["alvos_macro"]["rv_us"], carteira["alvos_macro"]["rf_br"], carteira["alvos_macro"]["btc"]]
    })
    
    df_atual = pd.DataFrame({
        "Classe": ["Renda Variável BR", "Exterior", "Renda Fixa BR", "Bitcoin"],
        "Valor": [subtotal_rv_br, subtotal_us_brl, subtotal_rf, subtotal_btc]
    })
    
    CORES = ['#10b981', '#3b82f6', '#facc15', '#f97316'] 
    
    col_graf1, col_graf2 = st.columns(2)
    
    with col_graf1:
        st.markdown("<h4 style='text-align: center; color: #64748b;'>🎯 Distribuição Alvo</h4>", unsafe_allow_html=True)
        fig_alvo = px.pie(df_alvo, values="Percentual", names="Classe", hole=0.55, color_discrete_sequence=CORES)
        fig_alvo.update_traces(textposition='inside', textinfo='percent+label', showlegend=False)
        fig_alvo.update_layout(margin=dict(t=10, b=10, l=10, r=10), hovermode=False)
        st.plotly_chart(fig_alvo, use_container_width=True)
        
    with col_graf2:
        st.markdown("<h4 style='text-align: center; color: #64748b;'>📊 Distribuição Atual</h4>", unsafe_allow_html=True)
        fig_atual = px.pie(df_atual, values="Valor", names="Classe", hole=0.55, color_discrete_sequence=CORES)
        if total_patrimonio > 0:
            fig_atual.update_traces(textposition='inside', textinfo='percent+label', showlegend=False)
        fig_atual.update_layout(margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_atual, use_container_width=True)

    st.divider()

    def cor_alvo(atual, alvo):
        if atual < alvo - 1.0: return "🔴 Para trás (Aportar)"
        elif atual > alvo + 1.0: return "🟡 Acima do Alvo"
        return "🟢 Equilibrado"
    
    col_rv, col_us = st.columns(2, gap="large")
    col_rf, col_btc = st.columns(2, gap="large")

    with col_rv:
        with st.container(border=True):
            atual_macro = (subtotal_rv_br / total_patrimonio * 100) if total_patrimonio > 0 else 0.0
            alvo_macro = carteira["alvos_macro"]["rv_br"]
            st.subheader(f"🇧🇷 Renda Variável BR")
            st.markdown(f"**Macro:** Atual {atual_macro:.1f}% | Alvo **{alvo_macro:.1f}%** ➔ {cor_alvo(atual_macro, alvo_macro)}")
            st.divider()
            
            if carteira["rv_br"]:
                visao_br = st.radio("Distribuição Interna:", ["Atual (Em R$)", "Alvo (Em %)"], horizontal=True, key="visao_br")
                
                df_br_atual = []
                df_br_alvo = []
                soma_alvos_internos = 0.0
                
                for tick, qtd in carteira["rv_br"].items():
                    valor_total = precos_br[tick] * qtd
                    alvo_micro = carteira["alvos_ativos"]["rv_br"].get(tick, 0.0)
                    soma_alvos_internos += alvo_micro
                    
                    df_br_atual.append({"Ativo": tick, "Valor": valor_total})
                    df_br_alvo.append({"Ativo": tick, "Percentual": alvo_micro})
                
                if visao_br == "Atual (Em R$)":
                    fig_br = px.pie(pd.DataFrame(df_br_atual), values="Valor", names="Ativo", hole=0.55)
                else:
                    fig_br = px.pie(pd.DataFrame(df_br_alvo), values="Percentual", names="Ativo", hole=0.55)
                    
                fig_br.update_traces(textposition='inside', textinfo='percent+label', showlegend=False)
                fig_br.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
                st.plotly_chart(fig_br, width="stretch")
                
                st.divider()
                
                for tick, qtd in carteira["rv_br"].items():
                    valor_total = precos_br[tick] * qtd
                    atual_micro = (valor_total / subtotal_rv_br * 100) if subtotal_rv_br > 0 else 0.0
                    alvo_micro = carteira["alvos_ativos"]["rv_br"].get(tick, 0.0)
                    
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"**{tick}**: {atual_micro:.1f}% (Alvo: {alvo_micro:.1f}%) ➔ {cor_alvo(atual_micro, alvo_micro)}")
                        st.caption(f"Total: R$ {valor_total:,.2f}")
                    with c2:
                        chave = f"w_br_{tick}"
                        st.number_input("Qtd", min_value=0.0, value=float(qtd), step=0.0001, format="%0.4f", key=chave, 
                                        on_change=atualizar_quantidade, args=("rv_br", tick, chave), 
                                        label_visibility="collapsed")
                
                st.info(f"Subtotal: R$ {subtotal_rv_br:,.2f} | Soma Alvos Internos: {soma_alvos_internos:.1f}%")
            else:
                st.write("Nenhum ativo.")

    with col_us:
        with st.container(border=True):
            atual_macro = (subtotal_us_brl / total_patrimonio * 100) if total_patrimonio > 0 else 0.0
            alvo_macro = carteira["alvos_macro"]["rv_us"]
            st.subheader(f"🇺🇸 Exterior")
            st.markdown(f"**Macro:** Atual {atual_macro:.1f}% | Alvo **{alvo_macro:.1f}%** ➔ {cor_alvo(atual_macro, alvo_macro)}")
            st.divider()
            
            if carteira["rv_us"]:
                visao_us = st.radio("Distribuição Interna:", ["Atual (Em US$)", "Alvo (Em %)"], horizontal=True, key="visao_us")
                
                df_us_atual = []
                df_us_alvo = []
                soma_alvos_internos = 0.0
                
                for tick, qtd in carteira["rv_us"].items():
                    valor_total_usd = precos_us[tick] * qtd
                    alvo_micro = carteira["alvos_ativos"]["rv_us"].get(tick, 0.0)
                    soma_alvos_internos += alvo_micro
                    
                    df_us_atual.append({"Ativo": tick, "Valor": valor_total_usd})
                    df_us_alvo.append({"Ativo": tick, "Percentual": alvo_micro})
                
                if visao_us == "Atual (Em US$)":
                    fig_us = px.pie(pd.DataFrame(df_us_atual), values="Valor", names="Ativo", hole=0.55)
                else:
                    fig_us = px.pie(pd.DataFrame(df_us_alvo), values="Percentual", names="Ativo", hole=0.55)
                    
                fig_us.update_traces(textposition='inside', textinfo='percent+label', showlegend=False)
                fig_us.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
                st.plotly_chart(fig_us, width="stretch")
                
                st.divider()
                
                for tick, qtd in carteira["rv_us"].items():
                    valor_total_usd = precos_us[tick] * qtd
                    atual_micro = (valor_total_usd / subtotal_us_usd * 100) if subtotal_us_usd > 0 else 0.0
                    alvo_micro = carteira["alvos_ativos"]["rv_us"].get(tick, 0.0)
                    
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"**{tick}**: {atual_micro:.1f}% (Alvo: {alvo_micro:.1f}%) ➔ {cor_alvo(atual_micro, alvo_micro)}")
                        st.caption(f"Total: US$ {valor_total_usd:,.2f}")
                    with c2:
                        chave = f"w_us_{tick}"
                        st.number_input("Qtd", min_value=0.0, value=float(qtd), step=0.0001, format="%0.4f", key=chave, 
                                        on_change=atualizar_quantidade, args=("rv_us", tick, chave), 
                                        label_visibility="collapsed")
                
                st.info(f"Subtotal: US$ {subtotal_us_usd:,.2f} (~R$ {subtotal_us_brl:,.2f}) | Soma Alvos Internos: {soma_alvos_internos:.1f}%")
            else:
                st.write("Nenhum ativo.")

    with col_rf:
        with st.container(border=True):
            atual_macro = (subtotal_rf / total_patrimonio * 100) if total_patrimonio > 0 else 0.0
            alvo_macro = carteira["alvos_macro"]["rf_br"]
            st.subheader(f"🛡️ Renda Fixa BR")
            st.markdown(f"**Macro:** Atual {atual_macro:.1f}% | Alvo **{alvo_macro:.1f}%** ➔ {cor_alvo(atual_macro, alvo_macro)}")
            st.divider()
            
            if carteira["rf_br"]:
                for titulo, valor in carteira["rf_br"].items():
                    st.markdown(f"**{titulo}**: R$ {valor:,.2f}")
                st.info(f"Subtotal: R$ {subtotal_rf:,.2f}")
            else:
                st.write("Nenhum ativo.")

    with col_btc:
        with st.container(border=True):
            atual_macro = (subtotal_btc / total_patrimonio * 100) if total_patrimonio > 0 else 0.0
            alvo_macro = carteira["alvos_macro"]["btc"]
            st.subheader(f"₿ Bitcoin")
            st.markdown(f"**Macro:** Atual {atual_macro:.1f}% | Alvo **{alvo_macro:.1f}%** ➔ {cor_alvo(atual_macro, alvo_macro)}")
            st.divider()
            
            if qtd_btc > 0:
                st.markdown(f"**Saldo:** {qtd_btc} BTC")
                st.caption(f"Cotação: R$ {preco_btc:,.2f}")
                st.info(f"Subtotal: R$ {subtotal_btc:,.2f}")
            else:
                st.write("Sem exposição.")
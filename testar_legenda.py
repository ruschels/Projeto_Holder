import os
import re
import sys

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YT_TRANSCRIPT = True
except ImportError:
    HAS_YT_TRANSCRIPT = False

try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

def extrair_id_video(url_ou_id):
    url_ou_id = url_ou_id.strip()
    if "v=" in url_ou_id:
        return url_ou_id.split("v=")[1].split("&")[0][:11]
    elif "shorts/" in url_ou_id:
        return url_ou_id.split("shorts/")[1].split("?")[0][:11]
    elif "youtu.be/" in url_ou_id:
        return url_ou_id.split("youtu.be/")[1].split("?")[0][:11]
    elif len(url_ou_id) == 11:
        return url_ou_id
    return url_ou_id[:11]

def buscar_legenda(url_ou_id):
    video_id = extrair_id_video(url_ou_id)
    texto_completo = ""

    if HAS_YT_TRANSCRIPT:
        try:
            lista_transcricoes = YouTubeTranscriptApi.list_transcripts(video_id)
            
            try:
                # 1. Tenta puxar explicitamente o português nativo ou automático
                transcricao = lista_transcricoes.find_transcript(['pt-BR', 'pt'])
            except:
                # 2. Se não existir PT, pega a primeira disponível e traduz na hora para PT
                transcricao_base = list(lista_transcricoes)[0]
                transcricao = transcricao_base.translate('pt')

            dados = transcricao.fetch()
            texto_completo = " ".join([item['text'] for item in dados])
            
            if texto_completo.strip():
                print(f"    [✅] Sucesso (youtube_transcript_api)! Idioma: {transcricao.language_code}")
        except Exception:
            pass 

    # O fallback do yt-dlp continua igual caso a primeira API falhe completamente
    if not texto_completo.strip() and HAS_YTDLP:
        try:
            ydl_opts = {
                'skip_download': True,
                'writesubtitles': False,
                'writeautomaticsubs': False,
                'subtitleslangs': ['pt', 'pt-BR', 'en', 'es'],
                'quiet': True,
            }
            url = f"https://www.youtube.com/watch?v={video_id}"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                subs = info.get('subtitles', {})
                auto_subs = info.get('automatic_captions', {})
                
                target_url = None
                for pool in [subs, auto_subs]:
                    # Aqui o seu código já está certo: ele varre buscando PT primeiro
                    for lang in ['pt', 'pt-BR', 'en', 'es']:
                        if lang in pool and pool[lang]:
                            for s in pool[lang]:
                                if s.get('ext') in ['json3', 'vtt', 'srv3']:
                                    target_url = s.get('url')
                                    break
                        if target_url: break
                    if target_url: break
                
                if target_url:
                    import requests
                    resp = requests.get(target_url, timeout=10)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            pedacos = []
                            for event in data.get('events', []):
                                for seg in event.get('segs', []):
                                    if 'utf8' in seg:
                                        t_seg = seg['utf8'].replace('\n', ' ').strip()
                                        if t_seg: pedacos.append(t_seg)
                            if pedacos:
                                texto_completo = " ".join(pedacos)
                                print("    [✅] Sucesso via yt-dlp (JSON3)!")
                        except Exception:
                            text_content = resp.text
                            lines = text_content.split('\n')
                            clean_lines = [l.strip() for l in lines if '-->' not in l and not l.strip().isdigit() and not l.startswith('WEBVTT')]
                            texto_completo = " ".join([l for l in clean_lines if l])
                            if texto_completo.strip():
                                print("    [✅] Sucesso via yt-dlp (Texto/VTT)!")
        except Exception:
            pass 

    if texto_completo.strip():
        return re.sub(r'\s+', ' ', texto_completo).strip()
    else:
        return None

def baixar_legendas_do_canal(url_canal, limite=10):
    if not HAS_YTDLP:
        print("❌ ERRO: A biblioteca yt_dlp é obrigatória.")
        return

    url_fix = url_canal.strip()
    if not any(aba in url_fix.lower() for aba in ["/videos", "/shorts", "/streams", "/releases"]):
        url_fix = f"{url_fix.rstrip('/')}/videos"

    print(f"\n[⏳] Mapeando os últimos {limite} vídeos da URL: {url_fix}")

    ydl_opts = {
        'extract_flat': True, 
        'quiet': True,
        'playlist_items': f'1-{limite}',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        }
    }
    
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'
        print("    [🍪] Arquivo cookies.txt detectado! Usando para evitar bloqueios.")
    else:
        print("    [⚠️] Aviso: cookies.txt não encontrado na pasta. Caso não ache os vídeos, crie este arquivo.")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_fix, download=False)
    except Exception as e:
        print(f"❌ Erro ao acessar o canal: {e}")
        return

    entradas_iniciais = info.get('entries', [])
    vids_selecionados = []

    for entry in entradas_iniciais:
        vid_id = entry.get('id', '')
        if vid_id and len(vid_id) == 11:
            vids_selecionados.append(entry)

    if not vids_selecionados and entradas_iniciais:
        primeiro_id = entradas_iniciais[0].get('id', '')
        if len(primeiro_id) > 11 and primeiro_id.startswith('UC'):
            print(f"\n    [🔄] Redirecionamento detectado! Forçando a busca pela raiz do ID do canal: {primeiro_id}...")
            url_forca = f"https://www.youtube.com/channel/{primeiro_id}/videos"
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_forca = ydl.extract_info(url_forca, download=False)
                    for entry in info_forca.get('entries', []):
                        vid_id = entry.get('id', '')
                        if vid_id and len(vid_id) == 11:
                            vids_selecionados.append(entry)
            except Exception as e:
                print(f"    ❌ Erro na busca forçada: {e}")

    if not vids_selecionados:
        print("\n❌ Nenhum vídeo encontrado. Dica: atualize o yt-dlp usando o comando: pip install -U yt-dlp")
        return

    print(f"\n[🔥] Sucesso! Total de {len(vids_selecionados)} vídeos confirmados.\n")
    print("🔎 INSPECIONANDO OS VÍDEOS:")
    for v in vids_selecionados:
        v_id = v.get('id', 'SEM_ID')
        v_titulo = v.get('title', 'Sem título')
        print(f" - Título: {v_titulo} | ID: {v_id}")
    print("----------------------------------------\n")

    # ==========================================
    # PASTA DE DESTINO ALTERADA CONFORME SOLICITADO
    # ==========================================
    # ==========================================
    # PASTA DE DESTINO: "Legenda" (no mesmo local do script)
    # ==========================================
    diretorio_script = os.path.dirname(os.path.abspath(__file__))
    pasta_destino = os.path.join(diretorio_script, "Legenda")
    os.makedirs(pasta_destino, exist_ok=True)
    
    for i, video in enumerate(vids_selecionados, 1):
        video_id = video.get('id')
        titulo = video.get('title', f"video_desconhecido_{video_id}")
        
        print(f"[{i}/{len(vids_selecionados)}] Baixando: {titulo}")
        
        texto_legenda = buscar_legenda(video_id)
        
        if texto_legenda:
            titulo_limpo = re.sub(r'[\\/*?:"<>|]', "", titulo)
            titulo_limpo = titulo_limpo[:100].strip()
            
            nome_arquivo = f"{titulo_limpo} [{video_id}].txt"
            caminho_arquivo = os.path.join(pasta_destino, nome_arquivo)
            
            try:
                with open(caminho_arquivo, "w", encoding="utf-8") as f:
                    f.write(texto_legenda)
                print(f"    [💾] Salvo em: {caminho_arquivo}\n")
            except Exception as e:
                print(f"    [⚠️] Erro ao salvar arquivo: {e}\n")
        else:
            print("    [❌] Nenhuma legenda encontrada para este vídeo.\n")

if __name__ == "__main__":
    print("========================================")
    print("   EXTRATOR DE LEGENDAS DE CANAIS YT    ")
    print("========================================")
    print("Dica: Você pode colar apenas o link do canal (ex: https://youtube.com/@Canal)")
    print("----------------------------------------")
    
    link = input("Cole o link do Canal: ")
    if link.strip():
        qtd_input = input("Quantos vídeos mais recentes você deseja buscar? (Aperte Enter para buscar 10): ")
        
        try:
            limite = int(qtd_input) if qtd_input.strip() else 10
        except ValueError:
            print("⚠️ Valor inválido digitado. Usando 10 como padrão.")
            limite = 10
            
        baixar_legendas_do_canal(link, limite)
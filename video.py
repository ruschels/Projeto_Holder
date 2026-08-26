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
                    # Varre buscando PT primeiro
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

def baixar_legenda_unica(url_video):
    video_id = extrair_id_video(url_video)
    if not video_id or len(video_id) != 11:
        print("❌ Link inválido. Não foi possível extrair o ID do vídeo.")
        return

    print(f"\n[⏳] Buscando informações do vídeo ID: {video_id}")
    
    # Tenta descobrir o título real do vídeo para dar nome ao arquivo
    titulo = f"video_{video_id}"
    if HAS_YTDLP:
        try:
            ydl_opts = {'quiet': True, 'skip_download': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                if info and 'title' in info:
                    titulo = info['title']
        except Exception:
            pass

    print(f"[⏳] Baixando legenda: {titulo}")
    texto_legenda = buscar_legenda(video_id)
    
    if texto_legenda:
        # Limpa o título para evitar caracteres proibidos no Windows/Linux
        titulo_limpo = re.sub(r'[\\/*?:"<>|]', "", titulo)
        titulo_limpo = titulo_limpo[:100].strip()
        
        # Cria a pasta Legenda no mesmo local do script
        diretorio_script = os.path.dirname(os.path.abspath(__file__))
        pasta_destino = os.path.join(diretorio_script, "Legenda")
        os.makedirs(pasta_destino, exist_ok=True)
        
        nome_arquivo = f"{titulo_limpo} [{video_id}].txt"
        caminho_arquivo = os.path.join(pasta_destino, nome_arquivo)
        
        try:
            with open(caminho_arquivo, "w", encoding="utf-8") as f:
                f.write(texto_legenda)
            print(f"\n[🔥] Sucesso! Legenda salva em: {caminho_arquivo}\n")
        except Exception as e:
            print(f"\n[⚠️] Erro ao salvar arquivo: {e}\n")
    else:
        print("\n[❌] Nenhuma legenda encontrada para este vídeo.\n")

if __name__ == "__main__":
    print("========================================")
    print("  EXTRATOR DE LEGENDA DE VÍDEO ÚNICO YT ")
    print("========================================")
    print("Dica: Cole o link de um vídeo ou de um Short")
    print("----------------------------------------")
    
    link = input("Cole o link do vídeo: ")
    if link.strip():
        baixar_legenda_unica(link)
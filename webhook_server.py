import os
import json
import time
import uuid
import base64
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, stream_with_context
import requests
from flask_cors import CORS
import websocket # Importar a biblioteca websocket-client

# Carregar variáveis de ambiente
load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "sk_199f6ef74d434fc5e8c4916f6eac0a515ae1dcff6b7fc61a")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Sm1seazb4gs7RSlUVw7c")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_01jxf0xalwfwm8gp30wt7nj7zn")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123") # Senha padrão

app = Flask(__name__)
CORS(app)

# Armazenamento em memória para sessões e controle parental
sessions = {}
parental_controls = {
    "daily_limit": 100,  # Limite de interações por dia
    "blocked_words": ["palavra1", "palavra2"], # Exemplo de palavras bloqueadas
    "time_restrictions": {
        "start_hour": 0, # 00:00
        "end_hour": 24   # 24:00
    }
}

# --- Funções de Autenticação e Controle Parental ---
def authenticate_admin(auth_header):
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ")[1]
    return token == ADMIN_PASSWORD

def is_interaction_allowed():
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Verificar restrição de horário
    current_hour = now.hour
    if not (parental_controls["time_restrictions"]["start_hour"] <= current_hour < parental_controls["time_restrictions"]["end_hour"]):
        return False, "Fora do horário permitido para interações."

    # Inicializar contador diário se não existir
    if today_str not in sessions:
        sessions[today_str] = {"interactions": 0, "history": {}}

    # Verificar limite diário
    if sessions[today_str]["interactions"] >= parental_controls["daily_limit"]:
        return False, "Limite diário de interações atingido."

    return True, ""

def check_blocked_words(text):
    for word in parental_controls["blocked_words"]:
        if word.lower() in text.lower():
            return False, f"A palavra \'{word}\' é bloqueada."
    return True, ""

# --- Endpoints da API --- 

@app.route("/", methods=["GET"])
def home():
    return "Servidor Webhook do Espelho Encantado. Use /chat para interagir ou /admin para o painel de controle."

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message")
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not user_message:
        return jsonify({"error": "Mensagem do usuário não fornecida."}), 400

    allowed, reason = is_interaction_allowed()
    if not allowed:
        return jsonify({"error": reason}), 403

    allowed_words, reason_words = check_blocked_words(user_message)
    if not allowed_words:
        return jsonify({"error": reason_words}), 403

    # Atualizar contador de interações
    today_str = datetime.now().strftime("%Y-%m-%d")
    sessions[today_str]["interactions"] += 1

    # Inicializar histórico da sessão se não existir
    if session_id not in sessions[today_str]["history"]:
        sessions[today_str]["history"][session_id] = []

    # Adicionar mensagem do usuário ao histórico
    sessions[today_str]["history"][session_id].append({"role": "user", "content": user_message})

    # --- Usar WebSockets para interação em tempo real com o agente --- 
    try:
        # 1. Obter a URL assinada
        signed_url_endpoint = f"https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id={ELEVENLABS_AGENT_ID}"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY
        }
        signed_url_response = requests.get(signed_url_endpoint, headers=headers)
        
        # Adicionar logs detalhados para a resposta da URL assinada
        if signed_url_response.status_code != 200:
            print(f"Erro ao obter URL assinada: Status {signed_url_response.status_code}, Resposta: {signed_url_response.text}")
            signed_url_response.raise_for_status() # Levanta um erro para status de resposta ruins (4xx ou 5xx)

        signed_url_data = signed_url_response.json()
        ws_url = signed_url_data.get("url")

        if not ws_url:
            raise Exception("Não foi possível obter a URL assinada para o WebSocket.")
        
        ws = websocket.create_connection(ws_url)

        # Enviar mensagem de inicialização da conversa
        init_message = {
            "type": "conversation_initiation_client_data",
            "conversation_initiation_client_data": {
                "voice_id": ELEVENLABS_VOICE_ID,
                "model_id": "eleven_multilingual_v2", # Ou o modelo que você preferir
                "sample_rate": 44100,
                "user_id": session_id
            }
        }
        ws.send(json.dumps(init_message))

        # Enviar a mensagem do usuário via WebSocket
        user_audio_chunk_message = {
            "type": "user_audio_chunk",
            "user_audio_chunk": {
                "text": user_message
            }
        }
        ws.send(json.dumps(user_audio_chunk_message))

        def generate_audio_stream_ws():
            while True:
                try:
                    message = ws.recv()
                    if message:
                        data = json.loads(message)
                        if data.get("type") == "audio" and "audio_base_64" in data.get("audio_event", {}): # Se a mensagem contiver dados de áudio
                            yield base64.b64decode(data["audio_event"]["audio_base_64"])
                        elif data.get("type") == "agent_response" and data.get("agent_response_event", {}).get("is_final"): # Fim da conversa
                            break
                except websocket.WebSocketConnectionClosedClosedException:
                    print("Conexão WebSocket fechada.")
                    break
                except Exception as e:
                    print(f"Erro ao receber mensagem WebSocket: {e}")
                    break
            ws.close()

        return Response(stream_with_context(generate_audio_stream_ws()), mimetype="audio/mpeg")

    except requests.exceptions.RequestException as e_req:
        print(f"Erro ao obter URL assinada: {e_req}. Tentando API de TTS como fallback.")
        # Fallback para a API de TTS se a obtenção da URL assinada falhar
        try:
            tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            tts_json_data = {
                "text": user_message,
                "stream": True
            }
            response = requests.post(tts_url, headers=headers, json=tts_json_data, stream=True)
            response.raise_for_status()

            def generate_audio_stream_tts():
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk

            return Response(stream_with_context(generate_audio_stream_tts()), mimetype="audio/mpeg")

        except requests.exceptions.RequestException as e_tts:
            print(f"Erro ao usar a API de TTS: {e_tts}")
            return jsonify({"error": f"Falha ao gerar áudio: {e_tts}"}), 500

    except websocket.WebSocketException as e_ws:
        print(f"Erro WebSocket: {e_ws}. Tentando API de TTS como fallback.")
        # Fallback para a API de TTS se a conexão WebSocket falhar
        try:
            tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            tts_json_data = {
                "text": user_message,
                "stream": True
            }
            response = requests.post(tts_url, headers=headers, json=tts_json_data, stream=True)
            response.raise_for_status()

            def generate_audio_stream_tts():
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk

            return Response(stream_with_context(generate_audio_stream_tts()), mimetype="audio/mpeg")

        except requests.exceptions.RequestException as e_tts:
            print(f"Erro ao usar a API de TTS: {e_tts}")
            return jsonify({"error": f"Falha ao gerar áudio: {e_tts}"}), 500

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado. Forneça a senha de administração no cabeçalho Authorization: Bearer <senha>"}), 401

    if request.method == "POST":
        data = request.json
        if "daily_limit" in data: parental_controls["daily_limit"] = data["daily_limit"]
        if "blocked_words" in data: parental_controls["blocked_words"] = data["blocked_words"]
        if "time_restrictions" in data: parental_controls["time_restrictions"] = data["time_restrictions"]
        return jsonify({"message": "Configurações atualizadas com sucesso!", "current_controls": parental_controls})

    # Retornar status e configurações atuais
    return jsonify({
        "status": "Servidor ativo",
        "total_interactions_today": sessions.get(datetime.now().strftime("%Y-%m-%d"), {"interactions": 0})["interactions"],
        "parental_controls": parental_controls,
        "active_sessions": {date: {sess_id: len(sess_hist) for sess_id, sess_hist in data["history"].items()} for date, data in sessions.items()}
    })

@app.route("/api/interactions", methods=["GET"])
def get_interactions():
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    return jsonify(sessions)

@app.route("/api/controls", methods=["GET"])
def get_controls():
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    return jsonify(parental_controls)

@app.route("/api/sessions/<session_id>", methods=["GET"])
def get_session_history(session_id):
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    session_history = sessions.get(today_str, {}).get("history", {}).get(session_id)
    
    if not session_history:
        return jsonify({"error": "Sessão não encontrada ou sem histórico para hoje."}), 404
        
    return jsonify(session_history)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


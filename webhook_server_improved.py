import os
import json
import time
import uuid
import logging
import hmac
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, stream_with_context
import requests
from flask_cors import CORS

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('webhook_server')

# Carregar variáveis de ambiente
load_dotenv()

# Configurações da API ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Sm1seazb4gs7RSlUVw7c")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_01jxf0xalwfwm8gp30wt7nj7zn")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # Senha padrão
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Secret para verificação de webhooks

# Configurações da API
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
AGENT_CHAT_ENDPOINT = f"{ELEVENLABS_BASE_URL}/agents/{ELEVENLABS_AGENT_ID}/chat"
TTS_STREAM_ENDPOINT = f"{ELEVENLABS_BASE_URL}/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"

app = Flask(__name__)
CORS(app)  # Habilitar CORS para todas as rotas

# Armazenamento em memória para sessões e controle parental
sessions = {}
parental_controls = {
    "daily_limit": 100,  # Limite de interações por dia
    "blocked_words": ["palavra1", "palavra2"],  # Exemplo de palavras bloqueadas
    "time_restrictions": {
        "start_hour": 0,  # 00:00
        "end_hour": 24    # 24:00
    }
}

# --- Funções de Autenticação e Segurança ---
def authenticate_admin(auth_header):
    """Verifica se o cabeçalho de autenticação contém a senha de administrador válida."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ")[1]
    return token == ADMIN_PASSWORD

def verify_webhook_signature(request_data, signature_header):
    """Verifica a assinatura do webhook da ElevenLabs."""
    if not WEBHOOK_SECRET or not signature_header:
        logger.warning("Verificação de assinatura ignorada: secret ou cabeçalho ausente")
        return True  # Permitir se não houver secret configurado (para desenvolvimento)
    
    try:
        # Calcular assinatura usando HMAC SHA-256
        expected_signature = hmac.new(
            WEBHOOK_SECRET.encode(),
            request_data,
            hashlib.sha256
        ).hexdigest()
        
        # Comparar assinaturas com tempo constante para evitar timing attacks
        return hmac.compare_digest(expected_signature, signature_header)
    except Exception as e:
        logger.error(f"Erro ao verificar assinatura do webhook: {e}")
        return False

# --- Funções de Controle Parental ---
def is_interaction_allowed():
    """Verifica se a interação é permitida com base nas restrições parentais."""
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
    """Verifica se o texto contém palavras bloqueadas."""
    for word in parental_controls["blocked_words"]:
        if word.lower() in text.lower():
            return False, f"A palavra '{word}' é bloqueada."
    return True, ""

# --- Funções de Integração com ElevenLabs ---
def generate_audio_from_agent(user_message, messages, session_id):
    """Gera áudio usando a API de Agentes da ElevenLabs."""
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    
    json_data = {
        "text": user_message,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
        "stream": True,
        "messages": messages
    }
    
    try:
        logger.info(f"Chamando API de Agentes para sessão {session_id}")
        response = requests.post(
            AGENT_CHAT_ENDPOINT, 
            headers=headers, 
            json=json_data, 
            stream=True,
            timeout=30  # Timeout para evitar bloqueios longos
        )
        response.raise_for_status()
        logger.info(f"API de Agentes respondeu com status {response.status_code}")
        return response, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao chamar API de Agentes: {e}")
        return None, str(e)

def generate_audio_from_tts(text, session_id):
    """Gera áudio usando a API de Text-to-Speech da ElevenLabs como fallback."""
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    
    json_data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
    }
    
    try:
        logger.info(f"Chamando API de TTS (fallback) para sessão {session_id}")
        response = requests.post(
            TTS_STREAM_ENDPOINT, 
            headers=headers, 
            json=json_data, 
            stream=True,
            timeout=30
        )
        response.raise_for_status()
        logger.info(f"API de TTS respondeu com status {response.status_code}")
        return response, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao chamar API de TTS: {e}")
        return None, str(e)

# --- Endpoints da API --- 
@app.route("/", methods=["GET"])
def home():
    """Endpoint raiz com informações básicas sobre o servidor."""
    return jsonify({
        "name": "Servidor Webhook do Espelho Encantado",
        "version": "1.1.0",
        "status": "online",
        "endpoints": {
            "/chat": "Interagir com o agente conversacional",
            "/webhook": "Receber eventos da ElevenLabs",
            "/admin": "Painel de controle administrativo"
        }
    })

@app.route("/health", methods=["GET"])
def health_check():
    """Endpoint para verificação de saúde do servidor."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": time.time()
    })

@app.route("/chat", methods=["POST"])
def chat():
    """Endpoint principal para interação com o agente conversacional."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Dados JSON não fornecidos"}), 400
            
        user_message = data.get("message")
        session_id = data.get("session_id", str(uuid.uuid4()))
        
        if not user_message:
            return jsonify({"error": "Mensagem do usuário não fornecida"}), 400
        
        logger.info(f"Recebida mensagem para sessão {session_id}: {user_message[:50]}...")
        
        # Verificar controles parentais
        allowed, reason = is_interaction_allowed()
        if not allowed:
            logger.warning(f"Interação bloqueada: {reason}")
            return jsonify({"error": reason}), 403
        
        allowed_words, reason_words = check_blocked_words(user_message)
        if not allowed_words:
            logger.warning(f"Mensagem bloqueada: {reason_words}")
            return jsonify({"error": reason_words}), 403
        
        # Atualizar contador de interações
        today_str = datetime.now().strftime("%Y-%m-%d")
        sessions[today_str]["interactions"] += 1
        
        # Inicializar histórico da sessão se não existir
        if session_id not in sessions[today_str]["history"]:
            sessions[today_str]["history"][session_id] = []
        
        # Adicionar mensagem do usuário ao histórico
        sessions[today_str]["history"][session_id].append({"role": "user", "content": user_message})
        
        # Preparar histórico para a API da ElevenLabs
        messages = sessions[today_str]["history"][session_id]
        
        # Tentar usar a API de Agentes primeiro
        response, error = generate_audio_from_agent(user_message, messages, session_id)
        
        if response:
            def generate_audio_stream():
                try:
                    for chunk in response.iter_content(chunk_size=8192):  # Aumentado para melhor performance
                        if chunk:
                            yield chunk
                except Exception as e:
                    logger.error(f"Erro durante streaming de áudio: {e}")
            
            return Response(
                stream_with_context(generate_audio_stream()), 
                mimetype="audio/mpeg",
                headers={
                    "X-Session-ID": session_id,
                    "Cache-Control": "no-cache, no-store, must-revalidate"
                }
            )
        
        # Fallback para a API de TTS se a API de Agentes falhar
        logger.warning(f"Fallback para TTS devido a erro: {error}")
        fallback_text = f"Desculpe, não consegui processar sua mensagem através do agente. Aqui está uma resposta simples: Você disse '{user_message}'. Como posso ajudar?"
        
        tts_response, tts_error = generate_audio_from_tts(fallback_text, session_id)
        
        if tts_response:
            def generate_audio_stream_tts():
                try:
                    for chunk in tts_response.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                except Exception as e:
                    logger.error(f"Erro durante streaming de áudio TTS: {e}")
            
            # Adicionar resposta do sistema ao histórico
            sessions[today_str]["history"][session_id].append({"role": "assistant", "content": fallback_text})
            
            return Response(
                stream_with_context(generate_audio_stream_tts()), 
                mimetype="audio/mpeg",
                headers={
                    "X-Session-ID": session_id,
                    "X-Fallback": "true",
                    "Cache-Control": "no-cache, no-store, must-revalidate"
                }
            )
        
        # Se ambos falharem, retornar erro
        logger.error(f"Falha em ambas APIs (Agentes e TTS): {error}, {tts_error}")
        return jsonify({
            "error": "Falha ao gerar áudio",
            "agent_error": error,
            "tts_error": tts_error
        }), 500
        
    except Exception as e:
        logger.exception(f"Erro não tratado no endpoint /chat: {e}")
        return jsonify({"error": f"Erro interno do servidor: {str(e)}"}), 500

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """Manipula webhooks recebidos da ElevenLabs."""
    try:
        # Verificar assinatura do webhook
        signature = request.headers.get("X-ElevenLabs-Signature")
        raw_data = request.get_data()
        
        if not verify_webhook_signature(raw_data, signature):
            logger.warning("Assinatura de webhook inválida")
            return jsonify({"error": "Assinatura inválida"}), 401
        
        data = request.json
        event_type = data.get("event_type", "unknown")
        
        logger.info(f"Webhook recebido: {event_type}")
        logger.debug(f"Dados do webhook: {json.dumps(data, indent=2)}")
        
        # Processar diferentes tipos de eventos
        if event_type == "speech.started":
            # Lógica para quando a fala começa
            logger.info(f"Fala iniciada para ID: {data.get('speech_id')}")
        elif event_type == "speech.completed":
            # Lógica para quando a fala termina
            logger.info(f"Fala concluída para ID: {data.get('speech_id')}")
        elif event_type == "speech.error":
            # Lógica para quando ocorre um erro na fala
            logger.error(f"Erro na fala para ID: {data.get('speech_id')}, erro: {data.get('error')}")
        
        # Aqui você pode adicionar lógica específica para diferentes tipos de eventos
        
        return jsonify({
            "status": "success", 
            "message": f"Webhook {event_type} processado com sucesso",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.exception(f"Erro ao processar webhook: {e}")
        return jsonify({"error": f"Erro ao processar webhook: {str(e)}"}), 500

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    """Painel administrativo para gerenciamento de configurações."""
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        logger.warning("Tentativa de acesso não autorizado ao painel admin")
        return jsonify({
            "error": "Não autorizado. Forneça a senha de administração no cabeçalho Authorization: Bearer <senha>"
        }), 401

    if request.method == "POST":
        try:
            data = request.json
            changes = []
            
            if "daily_limit" in data:
                old_limit = parental_controls["daily_limit"]
                parental_controls["daily_limit"] = data["daily_limit"]
                changes.append(f"Limite diário alterado de {old_limit} para {data['daily_limit']}")
                
            if "blocked_words" in data:
                old_words = set(parental_controls["blocked_words"])
                new_words = set(data["blocked_words"])
                added = new_words - old_words
                removed = old_words - new_words
                
                if added:
                    changes.append(f"Palavras adicionadas: {', '.join(added)}")
                if removed:
                    changes.append(f"Palavras removidas: {', '.join(removed)}")
                    
                parental_controls["blocked_words"] = data["blocked_words"]
                
            if "time_restrictions" in data:
                old_start = parental_controls["time_restrictions"]["start_hour"]
                old_end = parental_controls["time_restrictions"]["end_hour"]
                new_start = data["time_restrictions"].get("start_hour", old_start)
                new_end = data["time_restrictions"].get("end_hour", old_end)
                
                if old_start != new_start or old_end != new_end:
                    changes.append(f"Horário alterado de {old_start}:00-{old_end}:00 para {new_start}:00-{new_end}:00")
                    
                parental_controls["time_restrictions"] = data["time_restrictions"]
            
            logger.info(f"Configurações atualizadas: {', '.join(changes)}")
            return jsonify({
                "message": "Configurações atualizadas com sucesso!",
                "changes": changes,
                "current_controls": parental_controls
            })
            
        except Exception as e:
            logger.exception(f"Erro ao atualizar configurações: {e}")
            return jsonify({"error": f"Erro ao atualizar configurações: {str(e)}"}), 500

    # Retornar status e configurações atuais
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_data = sessions.get(today_str, {"interactions": 0, "history": {}})
    
    return jsonify({
        "status": "Servidor ativo",
        "version": "1.1.0",
        "uptime": time.time(),
        "total_interactions_today": today_data["interactions"],
        "parental_controls": parental_controls,
        "active_sessions": {
            sess_id: len(sess_hist) 
            for sess_id, sess_hist in today_data.get("history", {}).items()
        },
        "elevenlabs_config": {
            "agent_id": ELEVENLABS_AGENT_ID,
            "voice_id": ELEVENLABS_VOICE_ID,
            "webhook_configured": bool(WEBHOOK_SECRET)
        }
    })

@app.route("/api/interactions", methods=["GET"])
def get_interactions():
    """Retorna dados de interações para administradores."""
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    
    # Opcionalmente filtrar por data
    date_filter = request.args.get("date")
    if date_filter:
        if date_filter in sessions:
            return jsonify({date_filter: sessions[date_filter]})
        return jsonify({"error": f"Nenhum dado encontrado para a data {date_filter}"}), 404
    
    return jsonify(sessions)

@app.route("/api/controls", methods=["GET"])
def get_controls():
    """Retorna configurações de controle parental para administradores."""
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    return jsonify(parental_controls)

@app.route("/api/sessions/<session_id>", methods=["GET"])
def get_session_history(session_id):
    """Retorna histórico de uma sessão específica para administradores."""
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    
    date_filter = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    session_history = sessions.get(date_filter, {}).get("history", {}).get(session_id)
    
    if not session_history:
        return jsonify({
            "error": f"Sessão não encontrada ou sem histórico para {date_filter}."
        }), 404
        
    return jsonify({
        "session_id": session_id,
        "date": date_filter,
        "message_count": len(session_history),
        "history": session_history
    })

@app.route("/api/test-tts", methods=["POST"])
def test_tts():
    """Endpoint para testar a API de TTS diretamente."""
    auth_header = request.headers.get("Authorization")
    if not authenticate_admin(auth_header):
        return jsonify({"error": "Não autorizado."}), 401
    
    try:
        data = request.json
        text = data.get("text")
        voice_id = data.get("voice_id", ELEVENLABS_VOICE_ID)
        
        if not text:
            return jsonify({"error": "Texto não fornecido"}), 400
        
        logger.info(f"Testando TTS com voz {voice_id}")
        tts_response, tts_error = generate_audio_from_tts(text, "test-session")
        
        if tts_response:
            def generate_audio_stream_test():
                for chunk in tts_response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            
            return Response(
                stream_with_context(generate_audio_stream_test()), 
                mimetype="audio/mpeg"
            )
        
        return jsonify({"error": f"Falha ao gerar áudio: {tts_error}"}), 500
        
    except Exception as e:
        logger.exception(f"Erro ao testar TTS: {e}")
        return jsonify({"error": f"Erro ao testar TTS: {str(e)}"}), 500

@app.errorhandler(404)
def not_found(e):
    """Manipulador para rotas não encontradas."""
    return jsonify({"error": "Endpoint não encontrado"}), 404

@app.errorhandler(500)
def server_error(e):
    """Manipulador para erros internos do servidor."""
    logger.exception("Erro interno do servidor")
    return jsonify({"error": "Erro interno do servidor"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Iniciando servidor webhook na porta {port}")
    app.run(host="0.0.0.0", port=port)

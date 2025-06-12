import os
import json
import time
import uuid
import base64
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, stream_with_context
import requests
from flask_cors import CORS
import websocket

# Carregar variáveis de ambiente
load_dotenv()

# Configurações da ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Sm1seazb4gs7RSlUVw7c")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_01jxf0xa1wfwm8gp30wt7nj7zn")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
CORS(app)

# Armazenamento em memória para sessões
sessions = {}

@app.route("/", methods=["GET"])
def home():
    return "Servidor Webhook do Espelho Encantado. Use /chat para interagir."

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "Olá")
    session_id = data.get("session_id", str(uuid.uuid4()))

    # --- Usar WebSockets para interação em tempo real com o agente --- 
    try:
        # 1. Obter a URL assinada - USANDO GET com params separados
        # Exatamente como orientado pelo suporte da ElevenLabs
        signed_url_endpoint = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY
        }
        params = {
            "agent_id": ELEVENLABS_AGENT_ID
        }
        
        print(f"Obtendo URL assinada para o agente: {ELEVENLABS_AGENT_ID}")
        print(f"Endpoint: {signed_url_endpoint}")
        print(f"Headers: {json.dumps({'xi-api-key': '****'})}")
        print(f"Params: {json.dumps(params)}")
        print(f"Método: GET")
        
        # IMPORTANTE: Usando GET com params separados
        signed_url_response = requests.get(
            signed_url_endpoint, 
            headers=headers,
            params=params
        )
        
        # Logs detalhados solicitados pelo suporte da ElevenLabs
        print(f"Status code: {signed_url_response.status_code}")
        print(f"Response text: {signed_url_response.text}")  # Não compartilhar API keys
        
        if signed_url_response.status_code != 200:
            print(f"Erro ao obter URL assinada: Status {signed_url_response.status_code}, Resposta: {signed_url_response.text}")
            signed_url_response.raise_for_status()

        signed_url_data = signed_url_response.json()
        ws_url = signed_url_data.get("url")

        if not ws_url:
            raise Exception("Não foi possível obter a URL assinada para o WebSocket.")
        
        print(f"URL WebSocket obtida: {ws_url}")
        
        # Estabelecer conexão WebSocket
        ws = websocket.create_connection(ws_url)

        # Enviar mensagem de inicialização da conversa
        init_message = {
            "type": "conversation_initiation_client_data",
            "conversation_initiation_client_data": {
                "voice_id": ELEVENLABS_VOICE_ID,
                "model_id": "eleven_multilingual_v2",
                "sample_rate": 44100,
                "user_id": session_id
            }
        }
        
        print(f"Enviando mensagem de inicialização: {json.dumps(init_message)}")
        ws.send(json.dumps(init_message))

        # Enviar a mensagem do usuário via WebSocket
        user_audio_chunk_message = {
            "type": "user_audio_chunk",
            "user_audio_chunk": {
                "text": user_message
            }
        }
        
        print(f"Enviando mensagem do usuário: {json.dumps(user_audio_chunk_message)}")
        ws.send(json.dumps(user_audio_chunk_message))

        def generate_audio_stream_ws():
            try:
                print("Iniciando stream de áudio...")
                
                while True:
                    try:
                        message = ws.recv()
                        if message:
                            data = json.loads(message)
                            message_type = data.get("type")
                            
                            print(f"Recebido mensagem do tipo: {message_type}")
                            
                            if message_type == "audio" and "audio_base_64" in data.get("audio_event", {}):
                                audio_data = base64.b64decode(data["audio_event"]["audio_base_64"])
                                yield audio_data
                            elif message_type == "agent_response" and data.get("agent_response_event", {}).get("is_final"):
                                break
                    except Exception as e:
                        print(f"Erro ao processar mensagem WebSocket: {e}")
                        break
            finally:
                try:
                    if ws:
                        ws.close()
                        print("Conexão WebSocket fechada.")
                except:
                    pass

        return Response(stream_with_context(generate_audio_stream_ws()), mimetype="audio/mpeg")

    except Exception as e:
        print(f"Erro: {e}")
        # Fallback para a API de TTS
        try:
            tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            tts_json_data = {
                "text": "Desculpe, não consegui me conectar ao meu cérebro mágico. Vou apenas repetir o que você disse: " + user_message,
                "model_id": "eleven_multilingual_v2",
                "stream": True
            }
            
            print(f"Usando fallback TTS com a mensagem: {tts_json_data['text']}")
            response = requests.post(tts_url, headers=headers, json=tts_json_data, stream=True)
            response.raise_for_status()

            def generate_audio_stream_tts():
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk

            return Response(stream_with_context(generate_audio_stream_tts()), mimetype="audio/mpeg")

        except Exception as e_tts:
            print(f"Erro ao usar a API de TTS: {e_tts}")
            return jsonify({"error": f"Falha ao gerar áudio: {e_tts}"}), 500

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    """
    Manipulador para eventos de webhook da ElevenLabs.
    Recebe notificações sobre eventos como Speech to Text, ConvAI Settings, etc.
    """
    try:
        data = request.json
        print(f"Webhook recebido: {json.dumps(data)}")
        
        # Aqui você pode processar diferentes tipos de eventos
        event_type = data.get("type")
        if event_type:
            print(f"Processando evento do tipo: {event_type}")
            # Implementar lógica específica para cada tipo de evento
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Erro ao processar webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

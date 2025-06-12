import os
import json
import time
import uuid
import base64
import logging
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, stream_with_context
import requests
from flask_cors import CORS
import websocket

# Configurar logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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

    try:
        signed_url_endpoint = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY
        }
        params = {
            "agent_id": ELEVENLABS_AGENT_ID
        }

        logging.info(f"Obtendo URL assinada para o agente: {ELEVENLABS_AGENT_ID}")
        logging.info(f"Endpoint: {signed_url_endpoint}")
        logging.info(f"Headers: {json.dumps({'xi-api-key': '****'})}")
        logging.info(f"Params: {json.dumps(params)}")
        logging.info(f"Método: GET")

        signed_url_response = requests.get(
            signed_url_endpoint, 
            headers=headers,
            params=params
        )

        logging.info(f"Status code: {signed_url_response.status_code}")
        logging.info(f"Response text: {signed_url_response.text}")

        if signed_url_response.status_code != 200:
            logging.error(f"Erro ao obter URL assinada: Status {signed_url_response.status_code}, Resposta: {signed_url_response.text}")
            signed_url_response.raise_for_status()

        signed_url_data = signed_url_response.json()
        ws_url = signed_url_data.get("url")

        if not ws_url:
            raise Exception("Não foi possível obter a URL assinada para o WebSocket.")
        
        logging.info(f"URL WebSocket obtida: {ws_url}")

        ws = websocket.create_connection(ws_url)

        init_message = {
            "type": "conversation_initiation_client_data",
            "conversation_initiation_client_data": {
                "voice_id": ELEVENLABS_VOICE_ID,
                "model_id": "eleven_multilingual_v2",
                "sample_rate": 44100,
                "user_id": session_id
            }
        }

        logging.info(f"Enviando mensagem de inicialização: {json.dumps(init_message)}")
        ws.send(json.dumps(init_message))

        user_audio_chunk_message = {
            "type": "user_audio_chunk",
            "user_audio_chunk": {
                "text": user_message
            }
        }

        logging.info(f"Enviando mensagem do usuário: {json.dumps(user_audio_chunk_message)}")
        ws.send(json.dumps(user_audio_chunk_message))

        def generate_audio_stream_ws():
            try:
                logging.info("Iniciando stream de áudio...")

                while True:
                    try:
                        message = ws.recv()
                        if message:
                            data = json.loads(message)
                            message_type = data.get("type")

                            logging.info(f"Recebido mensagem do tipo: {message_type}")

                            if message_type == "audio" and "audio_base_64" in data.get("audio_event", {}):
                                audio_data = base64.b64decode(data["audio_event"]["audio_base_64"])
                                yield audio_data
                            elif message_type == "agent_response" and data.get
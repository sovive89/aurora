import requests
import os
from dotenv import load_dotenv
import logging

# Setup básico
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
load_dotenv()

# Variáveis de ambiente
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")

# Endpoint oficial
signed_url_endpoint = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"

# Cabeçalhos e parâmetros
headers = {
    "xi-api-key": ELEVENLABS_API_KEY
}

params = {
    "agent_id": ELEVENLABS_AGENT_ID
}

# 🔍 Requisição de signed URL
try:
    logging.info("Solicitando signed URL da ElevenLabs...")
    logging.info(f"Endpoint: {signed_url_endpoint}")
    logging.info(f"Headers: {{'xi-api-key': '****'}}")
    logging.info(f"Params: {params}")

    response = requests.get(signed_url_endpoint, headers=headers, params=params)

    logging.info(f"Status code: {response.status_code}")
    logging.info(f"Response text: {response.text}")

    data = response.json()
    signed_url = data.get("signed_url")
    if signed_url:
        logging.info(f"Signed WebSocket URL: {signed_url}")
    else:
        logging.error("⚠️ Nenhuma signed_url retornada no JSON.")
except Exception as e:
    logging.error(f"Erro na requisição: {e}")
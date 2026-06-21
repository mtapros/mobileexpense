LM Studio Chat Android App

This is a separate Android Studio project for sending normal text prompts from an Android phone to LM Studio through a local FastAPI server.

How to use:
1. On the desktop, run the API server:
   - Install server dependencies if prompted: pip install fastapi uvicorn
   - Start it with: python exp.py --host 0.0.0.0 --port 8001
   - By default exp.py forwards chat to http://192.168.34.82:1234/v1/chat/completions and polls models from http://192.168.34.82:1234/v1/models.
   - Override with LMSTUDIO_ENDPOINT, LMSTUDIO_MODELS_ENDPOINT, LMSTUDIO_MODEL, LMSTUDIO_API_KEY, or LMSTUDIO_TIMEOUT_SECONDS if needed.
2. Open LmStudioChatApp in Android Studio and run it on a phone.
3. In the app, set Server URL to http://<desktop WireGuard or LAN IP>:8001.
4. Tap Check Chat Server to verify exp.py is reachable.
5. Tap Poll LM Studio Models to ask exp.py for the currently available LM Studio models.
6. Confirm or edit the Model ID field, type a prompt, then tap Send Query.

The app talks to exp.py using:
- GET /health to verify the server is reachable
- GET /models to poll LM Studio's OpenAI-compatible /v1/models endpoint
- POST /chat with JSON body {"query":"...","model":"optional-model-id"} to get the normal LM Studio chat result

It does not call LM Studio directly from the phone. Cleartext HTTP is enabled for local/WireGuard testing.

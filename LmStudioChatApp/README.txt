LM Studio Chat Android App

This is a separate Android Studio project for sending normal text prompts from an Android phone to LM Studio through a local FastAPI server.

How to use:
1. On the desktop, run the new API server:
   - Install server dependencies if prompted: pip install fastapi uvicorn
   - Start it with: python exp.py --host 0.0.0.0 --port 8001
   - By default exp.py forwards to http://192.168.34.82:1234/v1/chat/completions and model Qwen/Qwen3.6-35B-A3B.
   - Override with LMSTUDIO_ENDPOINT, LMSTUDIO_MODEL, LMSTUDIO_API_KEY, or LMSTUDIO_TIMEOUT_SECONDS if needed.
2. Open LmStudioChatApp in Android Studio and run it on a phone.
3. In the app, set Server URL to http://<desktop WireGuard or LAN IP>:8001.
4. Tap Check Chat Server.
5. Tap Fetch Models to load the server's LM Studio model list, then pick a model from the phone if you want to override the server default for this request.
   - If you leave the Model field blank, exp.py falls back to its configured/default model.
   - If model fetch fails or comes back empty, the app shows the error/status so you can keep using the default workflow.
6. Type a prompt, then tap Send Query.

The app talks to exp.py using:
- GET /health to verify the server is reachable
- GET /models to fetch available LM Studio model IDs through the server proxy
- POST /chat with JSON body {"query":"..."} to get the normal LM Studio chat result
- POST /chat may also include an optional "model" field, for example {"query":"...","model":"Qwen/Qwen3.6-35B-A3B"}

It does not call LM Studio directly from the phone. Cleartext HTTP is enabled for local/WireGuard testing.

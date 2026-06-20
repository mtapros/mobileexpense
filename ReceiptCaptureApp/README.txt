Receipt Capture Android App

This is a complete Android Studio project for taking a receipt photo on an Android phone and uploading it to the local exp6.py FastAPI server.

How to use:
1. On the desktop, run exp6.py and start the Android API Server.
   - Install server dependencies if prompted: pip install fastapi uvicorn python-multipart
   - Use a LAN-reachable host such as 0.0.0.0 or your computer's Wi-Fi IP, port 8000.
2. Unzip this folder, open ReceiptCaptureApp in Android Studio, and run it on a phone.
3. In the app, set Server URL to http://<desktop LAN IP>:8000.
   - The app intentionally accepts only plain HTTP URLs on local/private network addresses.
4. Tap Check exp6.py Server, Take Receipt Photo, then Upload Receipt to exp6.py.

The app only talks to exp6.py:
- GET /health to verify the server is reachable
- POST /receipts/upload with multipart form field name "file"

It does not call LM Studio or any model endpoint directly.
Cleartext HTTP is enabled for local network testing.

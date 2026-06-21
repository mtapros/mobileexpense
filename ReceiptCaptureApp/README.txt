Receipt Capture Android App

This is a complete Android Studio project for taking a receipt photo on an Android phone, uploading it to the local exp7.py FastAPI server, and later reviewing processed receipts from the phone.

How to use:
1. On the desktop, run exp7.py and start the Android API Server.
   - Install server dependencies if prompted: pip install fastapi uvicorn python-multipart
   - Use a LAN-reachable host such as 0.0.0.0 or your computer's Wi-Fi IP, port 8000.
2. Unzip this folder, open ReceiptCaptureApp in Android Studio, and run it on a phone.
3. In the app, set Server URL to http://<desktop LAN IP>:8000.
   - The app intentionally accepts only plain HTTP URLs on local/private network addresses.
4. Tap Check Receipt Server.
5. Tap Fetch Models to load the receipt server's LM Studio model list, then pick a model from the phone if you want that upload processed with a specific model.
   - If you leave the Model field blank, exp7.py falls back to its configured/default model.
   - If model fetch fails or comes back empty, the app shows the error/status so you can keep using the default workflow.
6. Take a receipt photo, then tap Upload Receipt.
7. Later, tap Browse Processed Receipts to refresh pending receipts, open a receipt, view the extracted summary/image, and approve or mark it for correction.

The app talks to exp7.py using:
- GET /health to verify the server is reachable
- GET /models to fetch available LM Studio model IDs through the server proxy
- POST /receipts/upload with multipart form field name "file"
- POST /receipts/upload may also include an optional multipart field named "model"
- GET /receipts to list processed and pending receipts
- GET /receipts/{receipt_id} to load receipt details
- GET /receipts/{receipt_id}/image to display the stored receipt image
- POST /receipts/{receipt_id}/review to submit approval or correction notes

It does not call LM Studio directly from the phone. The receipt API server handles model lookup and receipt processing.
Cleartext HTTP is enabled for local network testing.

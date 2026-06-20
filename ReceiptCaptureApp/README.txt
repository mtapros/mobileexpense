Receipt Capture Android App

This is a complete Android Studio project for taking a receipt photo on an Android phone, uploading it to the local exp7.py FastAPI server, and later reviewing processed receipts from the phone.

How to use:
1. On the desktop, run exp7.py and start the Android API Server.
   - Install server dependencies if prompted: pip install fastapi uvicorn python-multipart
   - Use a LAN-reachable host such as 0.0.0.0 or your computer's Wi-Fi IP, port 8000.
2. Unzip this folder, open ReceiptCaptureApp in Android Studio, and run it on a phone.
3. In the app, set Server URL to http://<desktop LAN IP>:8000.
   - The app intentionally accepts only plain HTTP URLs on local/private network addresses.
4. Tap Check Receipt Server, Take Receipt Photo, then Upload Receipt.
5. Later, tap Browse Processed Receipts to refresh pending receipts, open a receipt, view the extracted summary/image, and approve or mark it for correction.

The app talks to exp7.py using:
- GET /health to verify the server is reachable
- POST /receipts/upload with multipart form field name "file"
- GET /receipts to list processed and pending receipts
- GET /receipts/{receipt_id} to load receipt details
- GET /receipts/{receipt_id}/image to display the stored receipt image
- POST /receipts/{receipt_id}/review to submit approval or correction notes

It does not call LM Studio or any model endpoint directly.
Cleartext HTTP is enabled for local network testing.

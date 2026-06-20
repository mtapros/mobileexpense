package com.mtapros.mobileexpense.receiptcapture;

import android.Manifest;
import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int CAMERA_PERMISSION_REQUEST = 100;
    private static final int CAPTURE_IMAGE_REQUEST = 101;
    private static final String DEFAULT_SERVER_URL = "http://192.168.34.44:8000";

    private EditText serverUrlEditText;
    private TextView statusTextView;
    private TextView resultTextView;
    private ImageView previewImageView;
    private Button captureButton;
    private Button uploadButton;
    private Button checkServerButton;
    private Uri latestPhotoUri;
    private String latestPhotoName;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        serverUrlEditText = findViewById(R.id.serverUrlEditText);
        statusTextView = findViewById(R.id.statusTextView);
        resultTextView = findViewById(R.id.resultTextView);
        previewImageView = findViewById(R.id.previewImageView);
        captureButton = findViewById(R.id.captureButton);
        uploadButton = findViewById(R.id.uploadButton);
        checkServerButton = findViewById(R.id.checkServerButton);

        captureButton.setOnClickListener(v -> captureReceiptPhoto());
        uploadButton.setOnClickListener(v -> uploadLatestReceipt());
        checkServerButton.setOnClickListener(v -> checkServerHealth());
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void captureReceiptPhoto() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION_REQUEST);
            return;
        }
        openCamera();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                openCamera();
            } else {
                showStatus("Camera permission denied.");
            }
        }
    }

    private void openCamera() {
        latestPhotoName = "receipt_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".jpg";
        ContentValues values = new ContentValues();
        values.put(MediaStore.Images.Media.DISPLAY_NAME, latestPhotoName);
        values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
        values.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/ReceiptCapture");

        latestPhotoUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (latestPhotoUri == null) {
            showStatus("Could not create a photo destination.");
            return;
        }

        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        intent.putExtra(MediaStore.EXTRA_OUTPUT, latestPhotoUri);
        if (intent.resolveActivity(getPackageManager()) == null) {
            showStatus("No camera app found on this phone.");
            return;
        }
        showStatus("Opening camera...");
        startActivityForResult(intent, CAPTURE_IMAGE_REQUEST);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != CAPTURE_IMAGE_REQUEST) {
            return;
        }
        if (resultCode == RESULT_OK && latestPhotoUri != null) {
            previewImageView.setImageURI(latestPhotoUri);
            uploadButton.setEnabled(true);
            showStatus("Photo captured. Tap Upload Receipt to send it to exp6.py.");
            resultTextView.setText(latestPhotoName);
        } else {
            showStatus("Photo capture cancelled.");
        }
    }

    private LocalServer validatedServer() throws Exception {
        String value = serverUrlEditText.getText().toString().trim();
        if (value.isEmpty()) {
            value = DEFAULT_SERVER_URL;
        }
        if (!value.toLowerCase(Locale.US).startsWith("http://")) {
            value = "http://" + value;
        }
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        URI uri = new URI(value);
        if (!"http".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null) {
            throw new Exception("Use a plain local HTTP URL like http://192.168.1.50:8000");
        }
        if (!isLocalNetworkHost(uri.getHost())) {
            throw new Exception("Server URL must point to a local/private network address.");
        }
        int port = uri.getPort() == -1 ? 8000 : uri.getPort();
        return new LocalServer(uri.getHost(), port);
    }

    private boolean isLocalNetworkHost(String host) {
        try {
            for (InetAddress address : InetAddress.getAllByName(host)) {
                if (address.isAnyLocalAddress() || address.isLoopbackAddress() || address.isLinkLocalAddress() || address.isSiteLocalAddress()) {
                    return true;
                }
            }
        } catch (Exception ignored) {
            return false;
        }
        return false;
    }

    private void checkServerHealth() {
        setBusy(true, "Checking exp6.py server...");
        executor.execute(() -> {
            try {
                HttpResult response = sendHttpRequest(validatedServer(), "GET", "/health", null, null, 5000, 10000);
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    throw new Exception("HTTP " + response.statusCode + ": " + response.body);
                }
                runOnUiThread(() -> {
                    resultTextView.setText(response.body);
                    showStatus("exp6.py server is reachable.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Server check failed", e);
            }
        });
    }

    private void uploadLatestReceipt() {
        if (latestPhotoUri == null) {
            Toast.makeText(this, "Take a receipt photo first", Toast.LENGTH_SHORT).show();
            return;
        }
        setBusy(true, "Uploading receipt to exp6.py...");
        executor.execute(() -> {
            try {
                String boundary = "ReceiptBoundary-" + UUID.randomUUID();
                byte[] body = buildMultipartBody(boundary);
                HttpResult response = sendHttpRequest(
                        validatedServer(),
                        "POST",
                        "/receipts/upload",
                        "multipart/form-data; boundary=" + boundary,
                        body,
                        10000,
                        60000
                );
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    throw new Exception("HTTP " + response.statusCode + ": " + response.body);
                }
                JSONObject json = new JSONObject(response.body);
                String jobId = json.optString("job_id", "uploaded");
                runOnUiThread(() -> {
                    resultTextView.setText(response.body);
                    showStatus("Receipt uploaded to exp6.py. Job: " + jobId);
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Upload failed", e);
            }
        });
    }

    private byte[] buildMultipartBody(String boundary) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        writeUtf8(output, "--" + boundary + "\r\n");
        writeUtf8(output, "Content-Disposition: form-data; name=\"file\"; filename=\"" + latestPhotoName + "\"\r\n");
        writeUtf8(output, "Content-Type: image/jpeg\r\n\r\n");
        copyPhotoTo(output);
        writeUtf8(output, "\r\n--" + boundary + "--\r\n");
        return output.toByteArray();
    }

    private void copyPhotoTo(OutputStream outputStream) throws Exception {
        ContentResolver resolver = getContentResolver();
        try (InputStream inputStream = new BufferedInputStream(resolver.openInputStream(latestPhotoUri))) {
            if (inputStream == null) {
                throw new Exception("Could not read captured photo.");
            }
            byte[] buffer = new byte[8192];
            int read;
            while ((read = inputStream.read(buffer)) != -1) {
                outputStream.write(buffer, 0, read);
            }
        }
    }

    private HttpResult sendHttpRequest(LocalServer server, String method, String path, String contentType, byte[] body, int connectTimeoutMs, int readTimeoutMs) throws Exception {
        byte[] requestBody = body == null ? new byte[0] : body;
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(server.host, server.port), connectTimeoutMs);
            socket.setSoTimeout(readTimeoutMs);
            OutputStream outputStream = socket.getOutputStream();
            writeUtf8(outputStream, method + " " + path + " HTTP/1.1\r\n");
            writeUtf8(outputStream, "Host: " + server.host + ":" + server.port + "\r\n");
            writeUtf8(outputStream, "Connection: close\r\n");
            if (contentType != null) {
                writeUtf8(outputStream, "Content-Type: " + contentType + "\r\n");
            }
            writeUtf8(outputStream, "Content-Length: " + requestBody.length + "\r\n\r\n");
            outputStream.write(requestBody);
            outputStream.flush();
            return readHttpResult(socket.getInputStream());
        }
    }

    private HttpResult readHttpResult(InputStream inputStream) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read;
        while ((read = inputStream.read(buffer)) != -1) {
            output.write(buffer, 0, read);
        }
        byte[] raw = output.toByteArray();
        String response = new String(raw, StandardCharsets.ISO_8859_1);
        int headerEnd = response.indexOf("\r\n\r\n");
        if (headerEnd < 0) {
            throw new Exception("Invalid HTTP response from exp6.py");
        }
        String[] headerLines = response.substring(0, headerEnd).split("\r\n");
        String[] statusParts = headerLines[0].split(" ", 3);
        if (statusParts.length < 2) {
            throw new Exception("Invalid HTTP status from exp6.py");
        }
        int statusCode = Integer.parseInt(statusParts[1]);
        byte[] bodyBytes = Arrays.copyOfRange(raw, headerEnd + 4, raw.length);
        return new HttpResult(statusCode, new String(bodyBytes, StandardCharsets.UTF_8));
    }

    private void writeUtf8(OutputStream outputStream, String value) throws Exception {
        outputStream.write(value.getBytes(StandardCharsets.UTF_8));
    }

    private static class LocalServer {
        final String host;
        final int port;

        LocalServer(String host, int port) {
            this.host = host;
            this.port = port;
        }
    }

    private static class HttpResult {
        final int statusCode;
        final String body;

        HttpResult(int statusCode, String body) {
            this.statusCode = statusCode;
            this.body = body;
        }
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            captureButton.setEnabled(!busy);
            uploadButton.setEnabled(!busy && latestPhotoUri != null);
            checkServerButton.setEnabled(!busy);
            if (message != null) {
                statusTextView.setText(message);
            }
        });
    }

    private void showStatus(String message) {
        runOnUiThread(() -> statusTextView.setText(message));
    }

    private void showError(String prefix, Exception e) {
        runOnUiThread(() -> {
            statusTextView.setText(prefix + ".");
            resultTextView.setText(e.toString());
            setBusy(false, null);
        });
    }
}

package com.mtapros.mobileexpense.receiptcapture;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.ContentValues;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    public static final String EXTRA_SERVER_URL = "com.mtapros.mobileexpense.receiptcapture.SERVER_URL";
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
    private Button browseReceiptsButton;
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
        browseReceiptsButton = findViewById(R.id.browseReceiptsButton);

        captureButton.setOnClickListener(v -> captureReceiptPhoto());
        uploadButton.setOnClickListener(v -> uploadLatestReceipt());
        checkServerButton.setOnClickListener(v -> checkServerHealth());
        browseReceiptsButton.setOnClickListener(v -> openReceiptBrowser());
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
            showStatus("Photo captured. Tap Upload Receipt to send it to the receipt server.");
            resultTextView.setText(latestPhotoName);
        } else {
            showStatus("Photo capture cancelled.");
        }
    }

    private void checkServerHealth() {
        setBusy(true, "Checking receipt server...");
        executor.execute(() -> {
            try {
                ReceiptApiClient client = new ReceiptApiClient(serverUrlEditText.getText().toString());
                JSONObject response = client.checkHealth();
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    resultTextView.setText(formatJson(response));
                    showStatus("Receipt server is reachable.");
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
        setBusy(true, "Uploading receipt...");
        executor.execute(() -> {
            try {
                ReceiptApiClient client = new ReceiptApiClient(serverUrlEditText.getText().toString());
                JSONObject json = client.uploadReceipt(getContentResolver(), latestPhotoUri, latestPhotoName);
                String jobId = json.optString("job_id", "uploaded");
                String receiptId = json.optString("receipt_id", jobId);
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    resultTextView.setText(formatJson(json));
                    showStatus("Receipt uploaded. Job: " + jobId + " · Review ID: " + receiptId);
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Upload failed", e);
            }
        });
    }

    private void openReceiptBrowser() {
        try {
            String serverUrl = ReceiptApiClient.normalizeAndValidateServerUrl(serverUrlEditText.getText().toString());
            serverUrlEditText.setText(serverUrl);
            Intent intent = new Intent(this, ReceiptListActivity.class);
            intent.putExtra(EXTRA_SERVER_URL, serverUrl);
            startActivity(intent);
        } catch (Exception e) {
            showError("Open receipts failed", e);
        }
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            captureButton.setEnabled(!busy);
            uploadButton.setEnabled(!busy && latestPhotoUri != null);
            checkServerButton.setEnabled(!busy);
            browseReceiptsButton.setEnabled(!busy);
            if (message != null) {
                statusTextView.setText(message);
            }
        });
    }

    private void showStatus(String message) {
        runOnUiThread(() -> statusTextView.setText(message));
    }

    private String formatJson(JSONObject json) {
        return ReceiptApiClient.prettyPrintJson(json);
    }

    private void showError(String prefix, Exception e) {
        runOnUiThread(() -> {
            statusTextView.setText(prefix + ".");
            resultTextView.setText(e.toString());
            setBusy(false, null);
        });
    }
}

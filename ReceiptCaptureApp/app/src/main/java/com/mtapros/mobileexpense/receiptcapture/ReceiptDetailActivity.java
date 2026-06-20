package com.mtapros.mobileexpense.receiptcapture;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class ReceiptDetailActivity extends Activity {
    private TextView titleTextView;
    private TextView statusTextView;
    private TextView imageStatusTextView;
    private ImageView receiptImageView;
    private TextView keyFieldsTextView;
    private TextView summaryJsonTextView;
    private TextView rawJsonTextView;
    private EditText notesEditText;
    private EditText correctedFieldsEditText;
    private Button refreshButton;
    private Button approveButton;
    private Button needsCorrectionButton;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private String serverUrl;
    private String receiptId;
    private ReceiptApiClient.ReceiptDetail currentReceipt;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_receipt_detail);

        serverUrl = getIntent().getStringExtra(MainActivity.EXTRA_SERVER_URL);
        receiptId = getIntent().getStringExtra(ReceiptListActivity.EXTRA_RECEIPT_ID);

        titleTextView = findViewById(R.id.titleTextView);
        statusTextView = findViewById(R.id.statusTextView);
        imageStatusTextView = findViewById(R.id.imageStatusTextView);
        receiptImageView = findViewById(R.id.receiptImageView);
        keyFieldsTextView = findViewById(R.id.keyFieldsTextView);
        summaryJsonTextView = findViewById(R.id.summaryJsonTextView);
        rawJsonTextView = findViewById(R.id.rawJsonTextView);
        notesEditText = findViewById(R.id.notesEditText);
        correctedFieldsEditText = findViewById(R.id.correctedFieldsEditText);
        refreshButton = findViewById(R.id.refreshButton);
        approveButton = findViewById(R.id.approveButton);
        needsCorrectionButton = findViewById(R.id.needsCorrectionButton);

        findViewById(R.id.backButton).setOnClickListener(v -> finish());
        refreshButton.setOnClickListener(v -> loadReceipt());
        approveButton.setOnClickListener(v -> submitReview("approved"));
        needsCorrectionButton.setOnClickListener(v -> submitReview("needs_correction"));

        titleTextView.setText(receiptId == null ? "Receipt Review" : receiptId);
        loadReceipt();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void loadReceipt() {
        setBusy(true, "Loading receipt details...");
        executor.execute(() -> {
            try {
                ReceiptApiClient client = new ReceiptApiClient(serverUrl);
                ReceiptApiClient.ReceiptDetail detail = client.getReceipt(receiptId);
                Bitmap imageBitmap = null;
                String imageMessage = "Stored receipt image loaded.";
                try {
                    byte[] imageBytes = client.getReceiptImage(receiptId);
                    imageBitmap = BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.length);
                    if (imageBitmap == null) {
                        imageMessage = "Could not decode receipt image.";
                    }
                } catch (Exception imageError) {
                    imageMessage = "Receipt image unavailable: " + imageError.getMessage();
                }
                Bitmap finalImageBitmap = imageBitmap;
                String finalImageMessage = imageMessage;
                runOnUiThread(() -> {
                    currentReceipt = detail;
                    bindReceipt(detail, finalImageBitmap, finalImageMessage);
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Could not load receipt", e);
            }
        });
    }

    private void bindReceipt(ReceiptApiClient.ReceiptDetail detail, Bitmap bitmap, String imageMessage) {
        String title = detail.filename == null || detail.filename.isEmpty() ? detail.receiptId : detail.filename;
        titleTextView.setText(title);
        StringBuilder status = new StringBuilder();
        status.append("Status: ").append(emptyOrFallback(detail.status, "unknown"));
        status.append(" • Review: ").append(emptyOrFallback(detail.approvalStatus, "pending_review"));
        if (detail.createdAt != null && !detail.createdAt.isEmpty()) {
            status.append("\nCreated: ").append(detail.createdAt);
        }
        if (detail.processedAt != null && !detail.processedAt.isEmpty()) {
            status.append("\nProcessed: ").append(detail.processedAt);
        }
        if (detail.approvedAt != null && !detail.approvedAt.isEmpty()) {
            status.append("\nApproved: ").append(detail.approvedAt);
        }
        if (detail.error != null && !detail.error.isEmpty()) {
            status.append("\nError: ").append(detail.error);
        }
        statusTextView.setText(status.toString());

        receiptImageView.setImageBitmap(bitmap);
        imageStatusTextView.setText(imageMessage);
        keyFieldsTextView.setText(buildKeyFields(detail.summary));
        summaryJsonTextView.setText(formatJson(detail.summary, "No extracted summary yet."));
        rawJsonTextView.setText(formatJson(detail.rawResult, "No raw extraction JSON is available yet."));
        notesEditText.setText(detail.reviewNotes == null ? "" : detail.reviewNotes);
        correctedFieldsEditText.setText(detail.correctedFields.length() == 0 ? "" : prettyPrintJson(detail.correctedFields));

        boolean reviewable = "complete".equalsIgnoreCase(detail.status);
        approveButton.setEnabled(reviewable);
        needsCorrectionButton.setEnabled(reviewable);
        if (!reviewable) {
            imageStatusTextView.append("\nReview actions will unlock after processing completes.");
        }
    }

    private void submitReview(String approvalStatus) {
        JSONObject correctedFields;
        String reviewNotes = notesEditText.getText().toString();
        try {
            correctedFields = parseCorrectedFieldsInput();
        } catch (Exception e) {
            Toast.makeText(this, e.getMessage(), Toast.LENGTH_LONG).show();
            return;
        }
        setBusy(true, "Submitting review...");
        executor.execute(() -> {
            try {
                ReceiptApiClient client = new ReceiptApiClient(serverUrl);
                JSONObject response = client.submitReview(
                        receiptId,
                        approvalStatus,
                        reviewNotes,
                        correctedFields
                );
                runOnUiThread(() -> {
                    Toast.makeText(
                            ReceiptDetailActivity.this,
                            "Review saved: " + response.optString("approval_status", approvalStatus),
                            Toast.LENGTH_SHORT
                    ).show();
                    loadReceipt();
                });
            } catch (Exception e) {
                showError("Could not submit review", e);
            }
        });
    }

    private JSONObject parseCorrectedFieldsInput() throws Exception {
        String value = correctedFieldsEditText.getText().toString().trim();
        if (value.isEmpty()) {
            return new JSONObject();
        }
        JSONObject object = new JSONObject(value);
        if (object.length() == 0) {
            return new JSONObject();
        }
        return object;
    }

    private String buildKeyFields(JSONObject summary) {
        if (summary == null || summary.length() == 0) {
            return "No extracted key fields yet.";
        }
        StringBuilder builder = new StringBuilder();
        appendField(builder, "Merchant", summary.optString("merchant_name"));
        appendField(builder, "Date", summary.optString("receipt_date"));
        appendField(builder, "Time", summary.optString("receipt_time"));
        appendField(builder, "Subtotal", summary.optString("subtotal"));
        appendField(builder, "Tax", summary.optString("tax"));
        appendField(builder, "Tip", summary.optString("tip"));
        appendField(builder, "Total", summary.optString("total"));
        appendField(builder, "Payment", summary.optString("payment_method"));
        appendField(builder, "Category", summary.optString("expense_category"));
        appendField(builder, "Confidence", summary.optString("confidence"));
        appendField(builder, "Notes", summary.optString("notes"));
        if (summary.has("items")) {
            if (builder.length() > 0) {
                builder.append("\n");
            }
            builder.append("Items: ").append(summary.optJSONArray("items"));
        }
        if (builder.length() == 0) {
            return "No extracted key fields yet.";
        }
        return builder.toString().trim();
    }

    private void appendField(StringBuilder builder, String label, String value) {
        if (value == null || value.isEmpty()) {
            return;
        }
        if (builder.length() > 0) {
            builder.append("\n");
        }
        builder.append(label).append(": ").append(value);
    }

    private String formatJson(JSONObject json, String fallback) {
        if (json == null || json.length() == 0) {
            return fallback;
        }
        return prettyPrintJson(json);
    }

    private String prettyPrintJson(JSONObject json) {
        try {
            return json.toString(2);
        } catch (Exception ignored) {
            return json.toString();
        }
    }

    private String emptyOrFallback(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            refreshButton.setEnabled(!busy);
            boolean reviewable = currentReceipt != null && "complete".equalsIgnoreCase(currentReceipt.status);
            approveButton.setEnabled(!busy && reviewable);
            needsCorrectionButton.setEnabled(!busy && reviewable);
            notesEditText.setEnabled(!busy);
            correctedFieldsEditText.setEnabled(!busy);
            if (message != null) {
                statusTextView.setText(message);
            }
        });
    }

    private void showError(String prefix, Exception e) {
        runOnUiThread(() -> {
            statusTextView.setText(prefix + ".");
            rawJsonTextView.setText(e.toString());
            setBusy(false, null);
        });
    }
}

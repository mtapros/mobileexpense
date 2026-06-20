package com.mtapros.mobileexpense.receiptcapture;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ListView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class ReceiptListActivity extends Activity {
    public static final String EXTRA_RECEIPT_ID = "com.mtapros.mobileexpense.receiptcapture.RECEIPT_ID";

    private TextView serverTextView;
    private TextView statusTextView;
    private TextView emptyTextView;
    private Button pendingButton;
    private Button allButton;
    private ListView receiptListView;
    private ReceiptListAdapter adapter;
    private final List<ReceiptApiClient.ReceiptListItem> receiptItems = new ArrayList<>();
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private String serverUrl;
    private boolean pendingOnly = true;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_receipt_list);

        serverUrl = getIntent().getStringExtra(MainActivity.EXTRA_SERVER_URL);
        serverTextView = findViewById(R.id.serverTextView);
        statusTextView = findViewById(R.id.statusTextView);
        emptyTextView = findViewById(R.id.emptyTextView);
        pendingButton = findViewById(R.id.pendingButton);
        allButton = findViewById(R.id.allButton);
        receiptListView = findViewById(R.id.receiptListView);

        serverTextView.setText(serverUrl == null ? "" : serverUrl);
        adapter = new ReceiptListAdapter(receiptItems);
        receiptListView.setAdapter(adapter);

        findViewById(R.id.backButton).setOnClickListener(v -> finish());
        pendingButton.setOnClickListener(v -> loadReceipts(true));
        allButton.setOnClickListener(v -> loadReceipts(false));
        receiptListView.setOnItemClickListener((parent, view, position, id) -> openReceipt(receiptItems.get(position)));

        loadReceipts(true);
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void loadReceipts(boolean onlyPendingReview) {
        pendingOnly = onlyPendingReview;
        setBusy(true, onlyPendingReview ? "Loading receipts pending review..." : "Loading all receipts...");
        executor.execute(() -> {
            try {
                ReceiptApiClient client = new ReceiptApiClient(serverUrl);
                List<ReceiptApiClient.ReceiptListItem> items = client.listReceipts(
                        onlyPendingReview ? "complete" : null,
                        onlyPendingReview ? "pending_review" : null
                );
                runOnUiThread(() -> {
                    receiptItems.clear();
                    receiptItems.addAll(items);
                    adapter.notifyDataSetChanged();
                    emptyTextView.setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
                    emptyTextView.setText(onlyPendingReview
                            ? "No completed receipts are waiting for review."
                            : "No receipts are available yet.");
                    statusTextView.setText(onlyPendingReview
                            ? "Showing completed receipts that still need phone review."
                            : "Showing all uploaded receipts and their current status.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Could not load receipts", e);
            }
        });
    }

    private void openReceipt(ReceiptApiClient.ReceiptListItem item) {
        Intent intent = new Intent(this, ReceiptDetailActivity.class);
        intent.putExtra(MainActivity.EXTRA_SERVER_URL, serverUrl);
        intent.putExtra(EXTRA_RECEIPT_ID, item.receiptId);
        startActivity(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        loadReceipts(pendingOnly);
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            pendingButton.setEnabled(!busy);
            allButton.setEnabled(!busy);
            receiptListView.setEnabled(!busy);
            if (message != null) {
                statusTextView.setText(message);
            }
        });
    }

    private void showError(String prefix, Exception e) {
        runOnUiThread(() -> {
            statusTextView.setText(prefix + ".");
            emptyTextView.setVisibility(View.VISIBLE);
            emptyTextView.setText(e.toString());
            setBusy(false, null);
        });
    }

    private class ReceiptListAdapter extends ArrayAdapter<ReceiptApiClient.ReceiptListItem> {
        ReceiptListAdapter(List<ReceiptApiClient.ReceiptListItem> items) {
            super(ReceiptListActivity.this, 0, items);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View view = convertView;
            if (view == null) {
                view = LayoutInflater.from(getContext()).inflate(R.layout.receipt_list_item, parent, false);
            }
            ReceiptApiClient.ReceiptListItem item = getItem(position);
            TextView titleTextView = view.findViewById(R.id.titleTextView);
            TextView subtitleTextView = view.findViewById(R.id.subtitleTextView);
            if (item != null) {
                String title = item.filename == null || item.filename.isEmpty() ? item.receiptId : item.filename;
                titleTextView.setText(title);
                StringBuilder subtitle = new StringBuilder();
                if (item.createdAt != null && !item.createdAt.isEmpty()) {
                    subtitle.append("Created ").append(item.createdAt);
                }
                if (item.status != null && !item.status.isEmpty()) {
                    if (subtitle.length() > 0) {
                        subtitle.append(" • ");
                    }
                    subtitle.append("Status: ").append(item.status);
                }
                if (item.approvalStatus != null && !item.approvalStatus.isEmpty()) {
                    if (subtitle.length() > 0) {
                        subtitle.append(" • ");
                    }
                    subtitle.append("Review: ").append(item.approvalStatus);
                }
                if (item.merchantName != null && !item.merchantName.isEmpty()) {
                    if (subtitle.length() > 0) {
                        subtitle.append("\n");
                    }
                    subtitle.append(item.merchantName);
                }
                if (item.total != null && !item.total.isEmpty()) {
                    if (item.merchantName != null && !item.merchantName.isEmpty()) {
                        subtitle.append(" • ");
                    } else if (subtitle.length() > 0) {
                        subtitle.append("\n");
                    }
                    subtitle.append("Total: ").append(item.total);
                }
                subtitleTextView.setText(subtitle.length() == 0 ? item.receiptId : subtitle.toString());
            }
            return view;
        }
    }
}

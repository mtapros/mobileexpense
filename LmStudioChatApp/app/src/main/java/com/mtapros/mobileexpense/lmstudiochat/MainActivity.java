package com.mtapros.mobileexpense.lmstudiochat;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private EditText serverUrlEditText;
    private EditText queryEditText;
    private TextView statusTextView;
    private TextView resultTextView;
    private Button checkServerButton;
    private Button sendButton;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        serverUrlEditText = findViewById(R.id.serverUrlEditText);
        queryEditText = findViewById(R.id.queryEditText);
        statusTextView = findViewById(R.id.statusTextView);
        resultTextView = findViewById(R.id.resultTextView);
        checkServerButton = findViewById(R.id.checkServerButton);
        sendButton = findViewById(R.id.sendButton);

        checkServerButton.setOnClickListener(v -> checkServerHealth());
        sendButton.setOnClickListener(v -> sendQuery());
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private void checkServerHealth() {
        setBusy(true, "Checking chat server...");
        executor.execute(() -> {
            try {
                LmStudioApiClient client = new LmStudioApiClient(serverUrlEditText.getText().toString());
                JSONObject response = client.checkHealth();
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    resultTextView.setText(LmStudioApiClient.prettyPrintJson(response));
                    showStatus("Chat server is reachable.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Server check failed", e);
            }
        });
    }

    private void sendQuery() {
        String query = queryEditText.getText().toString().trim();
        if (query.isEmpty()) {
            Toast.makeText(this, "Enter a query first", Toast.LENGTH_SHORT).show();
            return;
        }
        setBusy(true, "Waiting for LM Studio...");
        executor.execute(() -> {
            try {
                LmStudioApiClient client = new LmStudioApiClient(serverUrlEditText.getText().toString());
                JSONObject response = client.sendChat(query);
                String answer = response.optString("answer", "");
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    resultTextView.setText(answer.isEmpty() ? LmStudioApiClient.prettyPrintJson(response) : answer);
                    showStatus("Response received.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Chat request failed", e);
            }
        });
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            checkServerButton.setEnabled(!busy);
            sendButton.setEnabled(!busy);
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

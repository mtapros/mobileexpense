package com.mtapros.mobileexpense.lmstudiochat;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private EditText serverUrlEditText;
    private EditText modelEditText;
    private EditText queryEditText;
    private TextView statusTextView;
    private TextView resultTextView;
    private Button checkServerButton;
    private Button fetchModelsButton;
    private Button sendButton;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        serverUrlEditText = findViewById(R.id.serverUrlEditText);
        modelEditText = findViewById(R.id.modelEditText);
        queryEditText = findViewById(R.id.queryEditText);
        statusTextView = findViewById(R.id.statusTextView);
        resultTextView = findViewById(R.id.resultTextView);
        checkServerButton = findViewById(R.id.checkServerButton);
        fetchModelsButton = findViewById(R.id.fetchModelsButton);
        sendButton = findViewById(R.id.sendButton);

        checkServerButton.setOnClickListener(v -> checkServerHealth());
        fetchModelsButton.setOnClickListener(v -> fetchModels());
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

    private void fetchModels() {
        setBusy(true, "Fetching available models...");
        executor.execute(() -> {
            try {
                LmStudioApiClient client = new LmStudioApiClient(serverUrlEditText.getText().toString());
                List<String> models = client.listModels();
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    if (models.isEmpty()) {
                        resultTextView.setText("No models were returned by the server.");
                        showStatus("No models available.");
                    } else {
                        resultTextView.setText(joinLines(models));
                        showStatus("Found " + models.size() + " model(s).");
                        showModelPicker(models);
                    }
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Model fetch failed", e);
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
                JSONObject response = client.sendChat(query, modelEditText.getText().toString());
                String answer = response.optString("answer", "");
                String model = response.optString("model", "").trim();
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    if (!model.isEmpty()) {
                        modelEditText.setText(model);
                    }
                    resultTextView.setText(answer.isEmpty() ? LmStudioApiClient.prettyPrintJson(response) : answer);
                    showStatus(model.isEmpty() ? "Response received." : "Response received from " + model + ".");
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
            fetchModelsButton.setEnabled(!busy);
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

    private void showModelPicker(List<String> models) {
        String[] items = models.toArray(new String[0]);
        new AlertDialog.Builder(this)
                .setTitle("Select LM Studio Model")
                .setItems(items, (dialog, which) -> {
                    modelEditText.setText(items[which]);
                    showStatus("Selected model: " + items[which]);
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private String joinLines(List<String> values) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) {
                builder.append('\n');
            }
            builder.append(values.get(i));
        }
        return builder.toString();
    }
}

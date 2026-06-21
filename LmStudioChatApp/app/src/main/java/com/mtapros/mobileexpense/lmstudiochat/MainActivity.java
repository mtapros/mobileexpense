package com.mtapros.mobileexpense.lmstudiochat;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private EditText serverUrlEditText;
    private EditText modelEditText;
    private EditText queryEditText;
    private TextView statusTextView;
    private TextView resultTextView;
    private Button checkServerButton;
    private Button pollModelsButton;
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
        pollModelsButton = findViewById(R.id.pollModelsButton);
        sendButton = findViewById(R.id.sendButton);

        checkServerButton.setOnClickListener(v -> checkServerHealth());
        pollModelsButton.setOnClickListener(v -> pollModels());
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
                    String defaultModel = response.optString("default_model", "");
                    if (modelEditText.getText().toString().trim().isEmpty() && !defaultModel.isEmpty()) {
                        modelEditText.setText(defaultModel);
                    }
                    resultTextView.setText(LmStudioApiClient.prettyPrintJson(response));
                    showStatus("Chat server is reachable.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Server check failed", e);
            }
        });
    }

    private void pollModels() {
        setBusy(true, "Polling LM Studio models...");
        executor.execute(() -> {
            try {
                LmStudioApiClient client = new LmStudioApiClient(serverUrlEditText.getText().toString());
                JSONObject response = client.getModels();
                String formattedModels = formatModels(response);
                runOnUiThread(() -> {
                    serverUrlEditText.setText(client.getBaseUrl());
                    String selectedModel = pickInitialModel(response);
                    if (modelEditText.getText().toString().trim().isEmpty() && !selectedModel.isEmpty()) {
                        modelEditText.setText(selectedModel);
                    }
                    resultTextView.setText(formattedModels);
                    showStatus("Model polling complete.");
                    setBusy(false, null);
                });
            } catch (Exception e) {
                showError("Model polling failed", e);
            }
        });
    }

    private void sendQuery() {
        String query = queryEditText.getText().toString().trim();
        if (query.isEmpty()) {
            Toast.makeText(this, "Enter a query first", Toast.LENGTH_SHORT).show();
            return;
        }
        String model = modelEditText.getText().toString().trim();
        setBusy(true, "Waiting for LM Studio...");
        executor.execute(() -> {
            try {
                LmStudioApiClient client = new LmStudioApiClient(serverUrlEditText.getText().toString());
                JSONObject response = client.sendChat(query, model);
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

    private String formatModels(JSONObject response) {
        JSONArray models = response.optJSONArray("models");
        StringBuilder builder = new StringBuilder();
        builder.append("Available LM Studio models");
        int count = response.optInt("count", models == null ? 0 : models.length());
        builder.append(" (").append(count).append("):\n");
        if (models == null || models.length() == 0) {
            builder.append("No models returned. Make sure LM Studio's local server is running and a model is available.\n");
        } else {
            for (int i = 0; i < models.length(); i++) {
                builder.append(i + 1).append(". ").append(models.optString(i)).append('\n');
            }
        }
        builder.append("\nRaw response:\n").append(LmStudioApiClient.prettyPrintJson(response));
        return builder.toString();
    }

    private String pickInitialModel(JSONObject response) {
        String defaultModel = response.optString("default_model", "").trim();
        if (!defaultModel.isEmpty()) {
            return defaultModel;
        }
        JSONArray models = response.optJSONArray("models");
        if (models != null && models.length() > 0) {
            return models.optString(0, "").trim();
        }
        return "";
    }

    private void setBusy(boolean busy, String message) {
        runOnUiThread(() -> {
            checkServerButton.setEnabled(!busy);
            pollModelsButton.setEnabled(!busy);
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

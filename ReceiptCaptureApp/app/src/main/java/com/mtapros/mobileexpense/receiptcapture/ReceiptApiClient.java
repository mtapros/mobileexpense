package com.mtapros.mobileexpense.receiptcapture;

import android.content.ContentResolver;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

public class ReceiptApiClient {
    private static final String DEFAULT_SERVER_URL = "http://192.168.34.44:8000";
    private final LocalServer server;

    public ReceiptApiClient(String serverUrl) throws Exception {
        this.server = validatedServer(serverUrl);
    }

    public String getBaseUrl() {
        return "http://" + server.host + ":" + server.port;
    }

    public static String normalizeAndValidateServerUrl(String serverUrl) throws Exception {
        LocalServer server = validatedServer(serverUrl);
        return "http://" + server.host + ":" + server.port;
    }

    public static String prettyPrintJson(JSONObject json) {
        try {
            return json.toString(2);
        } catch (Exception ignored) {
            return json.toString();
        }
    }

    public JSONObject checkHealth() throws Exception {
        HttpResult response = sendHttpRequest("GET", "/health", null, null, 5000, 10000);
        return requireJsonObject(response, "Server check failed");
    }

    public JSONObject uploadReceipt(ContentResolver resolver, Uri photoUri, String fileName) throws Exception {
        String boundary = "ReceiptBoundary-" + UUID.randomUUID();
        byte[] body = buildMultipartBody(resolver, photoUri, fileName, boundary);
        HttpResult response = sendHttpRequest(
                "POST",
                "/receipts/upload",
                "multipart/form-data; boundary=" + boundary,
                body,
                10000,
                60000
        );
        return requireJsonObject(response, "Upload failed");
    }

    public List<ReceiptListItem> listReceipts(String status, String approvalStatus) throws Exception {
        StringBuilder path = new StringBuilder("/receipts");
        List<String> params = new ArrayList<>();
        if (status != null && !status.isEmpty()) {
            params.add("status=" + URLEncoder.encode(status, StandardCharsets.UTF_8.name()));
        }
        if (approvalStatus != null && !approvalStatus.isEmpty()) {
            params.add("approval_status=" + URLEncoder.encode(approvalStatus, StandardCharsets.UTF_8.name()));
        }
        if (!params.isEmpty()) {
            path.append("?").append(join(params, "&"));
        }
        HttpResult response = sendHttpRequest("GET", path.toString(), null, null, 5000, 15000);
        if (response.statusCode < 200 || response.statusCode >= 300) {
            throw new Exception("HTTP " + response.statusCode + ": " + response.bodyAsUtf8());
        }
        JSONArray array = new JSONArray(response.bodyAsUtf8());
        List<ReceiptListItem> items = new ArrayList<>();
        for (int i = 0; i < array.length(); i++) {
            JSONObject json = array.optJSONObject(i);
            if (json != null) {
                items.add(ReceiptListItem.fromJson(json));
            }
        }
        return items;
    }

    public ReceiptDetail getReceipt(String receiptId) throws Exception {
        HttpResult response = sendHttpRequest("GET", "/receipts/" + encodePathSegment(receiptId), null, null, 5000, 15000);
        return ReceiptDetail.fromJson(requireJsonObject(response, "Load receipt failed"));
    }

    public byte[] getReceiptImage(String receiptId) throws Exception {
        HttpResult response = sendHttpRequest("GET", "/receipts/" + encodePathSegment(receiptId) + "/image", null, null, 5000, 30000);
        if (response.statusCode < 200 || response.statusCode >= 300) {
            throw new Exception("HTTP " + response.statusCode + ": " + response.bodyAsUtf8());
        }
        return response.bodyBytes;
    }

    public JSONObject submitReview(String receiptId, String approvalStatus, String reviewNotes, JSONObject correctedFields) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("approval_status", approvalStatus);
        payload.put("review_notes", reviewNotes == null ? "" : reviewNotes.trim());
        if (correctedFields != null && correctedFields.length() > 0) {
            payload.put("corrected_fields", correctedFields);
        }
        HttpResult response = sendHttpRequest(
                "POST",
                "/receipts/" + encodePathSegment(receiptId) + "/review",
                "application/json; charset=utf-8",
                payload.toString().getBytes(StandardCharsets.UTF_8),
                5000,
                15000
        );
        return requireJsonObject(response, "Submit review failed");
    }

    private static LocalServer validatedServer(String value) throws Exception {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty()) {
            normalized = DEFAULT_SERVER_URL;
        }
        if (!normalized.toLowerCase(Locale.US).startsWith("http://")) {
            normalized = "http://" + normalized;
        }
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        URI uri = new URI(normalized);
        if (!"http".equalsIgnoreCase(uri.getScheme())
                || uri.getHost() == null
                || uri.getUserInfo() != null
                || uri.getQuery() != null
                || uri.getFragment() != null) {
            throw new Exception("Use a plain local HTTP URL like http://192.168.1.50:8000");
        }
        if (!isLocalNetworkHost(uri.getHost())) {
            throw new Exception("Server URL must point to a local/private network address.");
        }
        int port = uri.getPort() == -1 ? 8000 : uri.getPort();
        return new LocalServer(uri.getHost(), port);
    }

    private static boolean isLocalNetworkHost(String host) {
        try {
            for (InetAddress address : InetAddress.getAllByName(host)) {
                if (address.isAnyLocalAddress()
                        || address.isLoopbackAddress()
                        || address.isLinkLocalAddress()
                        || address.isSiteLocalAddress()) {
                    return true;
                }
            }
        } catch (Exception ignored) {
            return false;
        }
        return false;
    }

    private byte[] buildMultipartBody(ContentResolver resolver, Uri photoUri, String fileName, String boundary) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        writeUtf8(output, "--" + boundary + "\r\n");
        writeUtf8(output, "Content-Disposition: form-data; name=\"file\"; filename=\"" + (fileName == null ? "receipt.jpg" : fileName) + "\"\r\n");
        writeUtf8(output, "Content-Type: image/jpeg\r\n\r\n");
        copyPhotoTo(resolver, photoUri, output);
        writeUtf8(output, "\r\n--" + boundary + "--\r\n");
        return output.toByteArray();
    }

    private void copyPhotoTo(ContentResolver resolver, Uri photoUri, OutputStream outputStream) throws Exception {
        try (InputStream inputStream = new BufferedInputStream(resolver.openInputStream(photoUri))) {
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

    private HttpResult sendHttpRequest(String method, String path, String contentType, byte[] body, int connectTimeoutMs, int readTimeoutMs) throws Exception {
        byte[] requestBody = body == null ? new byte[0] : body;
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(server.host, server.port), connectTimeoutMs);
            socket.setSoTimeout(readTimeoutMs);
            OutputStream outputStream = socket.getOutputStream();
            writeUtf8(outputStream, method + " " + path + " HTTP/1.1\r\n");
            writeUtf8(outputStream, "Host: " + server.host + ":" + server.port + "\r\n");
            writeUtf8(outputStream, "Accept: application/json\r\n");
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
            throw new Exception("Invalid HTTP response from receipt server");
        }
        String[] headerLines = response.substring(0, headerEnd).split("\r\n");
        String[] statusParts = headerLines[0].split(" ", 3);
        if (statusParts.length < 2) {
            throw new Exception("Invalid HTTP status from receipt server");
        }
        int statusCode = Integer.parseInt(statusParts[1]);
        byte[] bodyBytes = Arrays.copyOfRange(raw, headerEnd + 4, raw.length);
        return new HttpResult(statusCode, bodyBytes);
    }

    private JSONObject requireJsonObject(HttpResult response, String action) throws Exception {
        if (response.statusCode < 200 || response.statusCode >= 300) {
            throw new Exception("HTTP " + response.statusCode + ": " + response.bodyAsUtf8());
        }
        return new JSONObject(response.bodyAsUtf8());
    }

    private void writeUtf8(OutputStream outputStream, String value) throws Exception {
        outputStream.write(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String join(List<String> values, String delimiter) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) {
                builder.append(delimiter);
            }
            builder.append(values.get(i));
        }
        return builder.toString();
    }

    private static String encodePathSegment(String value) throws Exception {
        return URLEncoder.encode(value, StandardCharsets.UTF_8.name()).replace("+", "%20");
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
        final byte[] bodyBytes;

        HttpResult(int statusCode, byte[] bodyBytes) {
            this.statusCode = statusCode;
            this.bodyBytes = bodyBytes;
        }

        String bodyAsUtf8() {
            return new String(bodyBytes, StandardCharsets.UTF_8);
        }
    }

    public static class ReceiptListItem {
        public final String receiptId;
        public final String filename;
        public final String createdAt;
        public final String processedAt;
        public final String status;
        public final String approvalStatus;
        public final String merchantName;
        public final String total;

        private ReceiptListItem(
                String receiptId,
                String filename,
                String createdAt,
                String processedAt,
                String status,
                String approvalStatus,
                String merchantName,
                String total
        ) {
            this.receiptId = receiptId;
            this.filename = filename;
            this.createdAt = createdAt;
            this.processedAt = processedAt;
            this.status = status;
            this.approvalStatus = approvalStatus;
            this.merchantName = merchantName;
            this.total = total;
        }

        static ReceiptListItem fromJson(JSONObject json) {
            return new ReceiptListItem(
                    json.optString("receipt_id"),
                    json.optString("filename"),
                    json.optString("created_at"),
                    json.optString("processed_at"),
                    json.optString("status"),
                    json.optString("approval_status"),
                    json.optString("merchant_name"),
                    json.optString("total")
            );
        }
    }

    public static class ReceiptDetail {
        public final String receiptId;
        public final String filename;
        public final String status;
        public final String approvalStatus;
        public final String createdAt;
        public final String processedAt;
        public final String approvedAt;
        public final String reviewNotes;
        public final String error;
        public final JSONObject summary;
        public final JSONObject rawResult;
        public final JSONObject correctedFields;

        private ReceiptDetail(
                String receiptId,
                String filename,
                String status,
                String approvalStatus,
                String createdAt,
                String processedAt,
                String approvedAt,
                String reviewNotes,
                String error,
                JSONObject summary,
                JSONObject rawResult,
                JSONObject correctedFields
        ) {
            this.receiptId = receiptId;
            this.filename = filename;
            this.status = status;
            this.approvalStatus = approvalStatus;
            this.createdAt = createdAt;
            this.processedAt = processedAt;
            this.approvedAt = approvedAt;
            this.reviewNotes = reviewNotes;
            this.error = error;
            this.summary = summary == null ? new JSONObject() : summary;
            this.rawResult = rawResult == null ? new JSONObject() : rawResult;
            this.correctedFields = correctedFields == null ? new JSONObject() : correctedFields;
        }

        static ReceiptDetail fromJson(JSONObject json) {
            return new ReceiptDetail(
                    json.optString("receipt_id"),
                    json.optString("filename"),
                    json.optString("status"),
                    json.optString("approval_status"),
                    json.optString("created_at"),
                    json.optString("processed_at"),
                    json.optString("approved_at"),
                    json.optString("review_notes"),
                    json.optString("error"),
                    json.optJSONObject("summary"),
                    json.optJSONObject("raw_result"),
                    json.optJSONObject("corrected_fields")
            );
        }
    }
}

package com.mtapros.mobileexpense.lmstudiochat;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Locale;

public class LmStudioApiClient {
    private final LocalServer server;

    public LmStudioApiClient(String serverUrl) throws Exception {
        this.server = validatedServer(serverUrl);
    }

    public String getBaseUrl() {
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

    public JSONObject sendChat(String query) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("query", query == null ? "" : query.trim());
        HttpResult response = sendHttpRequest(
                "POST",
                "/chat",
                "application/json; charset=utf-8",
                payload.toString().getBytes(StandardCharsets.UTF_8),
                10000,
                180000
        );
        return requireJsonObject(response, "Chat request failed");
    }

    private static LocalServer validatedServer(String value) throws Exception {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty()) {
            throw new Exception("Enter the chat server URL.");
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
            throw new Exception("Use a plain local HTTP URL like http://192.168.1.50:8001");
        }
        if (!isLocalNetworkHost(uri.getHost())) {
            throw new Exception("Server URL must point to a local/private network address.");
        }
        int port = uri.getPort() == -1 ? 8001 : uri.getPort();
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
            throw new Exception("Invalid HTTP response from chat server");
        }
        String[] headerLines = response.substring(0, headerEnd).split("\r\n");
        String[] statusParts = headerLines[0].split(" ", 3);
        if (statusParts.length < 2) {
            throw new Exception("Invalid HTTP status from chat server");
        }
        int statusCode = Integer.parseInt(statusParts[1]);
        byte[] bodyBytes = Arrays.copyOfRange(raw, headerEnd + 4, raw.length);
        return new HttpResult(statusCode, bodyBytes);
    }

    private JSONObject requireJsonObject(HttpResult response, String action) throws Exception {
        if (response.statusCode < 200 || response.statusCode >= 300) {
            throw new Exception(action + ": HTTP " + response.statusCode + ": " + response.bodyAsUtf8());
        }
        return new JSONObject(response.bodyAsUtf8());
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
        final byte[] bodyBytes;

        HttpResult(int statusCode, byte[] bodyBytes) {
            this.statusCode = statusCode;
            this.bodyBytes = bodyBytes;
        }

        String bodyAsUtf8() {
            return new String(bodyBytes, StandardCharsets.UTF_8);
        }
    }
}

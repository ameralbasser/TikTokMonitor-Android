package com.amer.tiktokmonitor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.graphics.Color;
import android.view.View;
import android.webkit.WebChromeClient;
import android.webkit.JsResult;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;
import com.chaquo.python.PyException;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;

public class MainActivity extends Activity {
    private static final String DASHBOARD_URL = "http://127.0.0.1:8765";
    private WebView webView;
    private TextView status;
    private volatile String startupError;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(11, 13, 18));
        getWindow().setNavigationBarColor(Color.rgb(11, 13, 18));
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(11, 13, 18));
        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(11, 13, 18));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setPositiveButton("موافق", (dialog, which) -> result.confirm())
                    .setOnCancelListener(dialog -> result.cancel())
                    .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setPositiveButton("موافق", (dialog, which) -> result.confirm())
                    .setNegativeButton("إلغاء", (dialog, which) -> result.cancel())
                    .setOnCancelListener(dialog -> result.cancel())
                    .show();
                return true;
            }
        });
        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void download(String path, String filename) {
                if (path == null || !(path.startsWith("/api/archive-zip?folder=")
                        || path.startsWith("/api/archive-file?path="))) {
                    runOnUiThread(() -> Toast.makeText(MainActivity.this,
                        "مسار التنزيل غير صالح", Toast.LENGTH_SHORT).show());
                    return;
                }
                String safeName = sanitizeFilename(filename);
                runOnUiThread(() -> Toast.makeText(MainActivity.this,
                    "جارٍ تجهيز التنزيل: " + safeName, Toast.LENGTH_SHORT).show());
                new Thread(() -> performArchiveDownload(path, safeName), "TikTokMonitor-Download").start();
            }
        }, "Android");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                status.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
            }
        });
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(16);
        status.setPadding(32, 32, 32, 32);
        status.setText("جاري تشغيل محرك المراقبة المدمج...");
        status.setGravity(android.view.Gravity.CENTER);
        status.setBackgroundColor(Color.rgb(11, 13, 18));
        root.addView(status, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        webView.setVisibility(View.INVISIBLE);
        startPythonServer();
    }

    private String sanitizeFilename(String name) {
        String value = name == null ? "TikTokArchive" : name.trim();
        StringBuilder clean = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            clean.append((c < 32 || c == '/' || c == '\\' || c == ':' ||
                c == '*' || c == '?' || c == '"' || c == '<' || c == '>' || c == '|')
                ? '_' : c);
        }
        String result = clean.toString().trim();
        if (result.isEmpty()) result = "TikTokArchive";
        if (result.length() > 140) result = result.substring(0, 140);
        return result;
    }

    private void performArchiveDownload(String path, String filename) {
        HttpURLConnection connection = null;
        Uri savedUri = null;
        File fallbackFile = null;
        try {
            connection = (HttpURLConnection) new URL(DASHBOARD_URL + path).openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(120000);
            connection.setRequestMethod("GET");
            connection.setUseCaches(false);
            int statusCode = connection.getResponseCode();
            if (statusCode < 200 || statusCode >= 300) {
                String detail = "HTTP " + statusCode;
                try (InputStream err = connection.getErrorStream()) {
                    if (err != null) {
                        byte[] bytes = new byte[1024];
                        int count = err.read(bytes);
                        if (count > 0) detail = new String(bytes, 0, count, java.nio.charset.StandardCharsets.UTF_8);
                    }
                } catch (Exception ignored) {}
                throw new IOException(detail);
            }

            String mime = path.startsWith("/api/archive-zip?")
                ? "application/zip" : "text/plain";
            OutputStream output;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.MediaColumns.DISPLAY_NAME, filename);
                values.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/TikTokMonitor");
                values.put(MediaStore.MediaColumns.IS_PENDING, 1);
                savedUri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (savedUri == null) throw new IOException("تعذر إنشاء الملف في التنزيلات");
                output = getContentResolver().openOutputStream(savedUri, "w");
                if (output == null) throw new IOException("تعذر فتح ملف التنزيل");
            } else {
                File base = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
                if (base == null) base = getFilesDir();
                File dir = new File(base, "TikTokMonitor");
                if (!dir.exists() && !dir.mkdirs()) throw new IOException("تعذر إنشاء مجلد التنزيلات");
                fallbackFile = new File(dir, filename);
                output = new FileOutputStream(fallbackFile);
            }

            try (InputStream input = connection.getInputStream(); OutputStream out = output) {
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) out.write(buffer, 0, count);
                out.flush();
            }

            if (savedUri != null) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.MediaColumns.IS_PENDING, 0);
                getContentResolver().update(savedUri, ready, null, null);
            }
            final String location = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                ? "التنزيلات/TikTokMonitor/" + filename
                : (fallbackFile == null ? filename : fallbackFile.getAbsolutePath());
            runOnUiThread(() -> Toast.makeText(MainActivity.this,
                "تم حفظ الملف: " + location, Toast.LENGTH_LONG).show());
        } catch (Exception e) {
            if (savedUri != null) {
                try { getContentResolver().delete(savedUri, null, null); } catch (Exception ignored) {}
            }
            String message = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            final String shown = message.length() > 180 ? message.substring(0, 180) : message;
            runOnUiThread(() -> new AlertDialog.Builder(MainActivity.this)
                .setTitle("تعذر تنزيل الأرشيف")
                .setMessage(shown)
                .setPositiveButton("موافق", null)
                .show());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }


    private void prepareFfmpegRuntime() throws IOException {
        File targetDir = new File(getFilesDir(), "ffmpeg");
        File marker = new File(targetDir, ".runtime-build-264");
        File binary = new File(targetDir, "ffmpeg");
        if (marker.isFile() && binary.isFile() && binary.length() > 100000 && binary.canExecute()) {
            return;
        }
        if (!targetDir.exists() && !targetDir.mkdirs()) {
            throw new IOException("تعذر إنشاء مجلد FFmpeg");
        }
        String[] names = getAssets().list("ffmpeg");
        if (names == null || names.length == 0) {
            throw new IOException("محرك FFmpeg غير مضمّن داخل APK");
        }
        for (String name : names) {
            if (name.contains("/") || name.contains("\\\\")) continue;
            File outFile = new File(targetDir, name);
            try (InputStream in = getAssets().open("ffmpeg/" + name);
                 OutputStream out = new FileOutputStream(outFile)) {
                byte[] buffer = new byte[32768];
                int count;
                while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
                out.flush();
            }
        }
        if (!binary.isFile() || binary.length() < 100000 || !binary.setExecutable(true, true)) {
            throw new IOException("تعذر تجهيز ملف FFmpeg التنفيذي");
        }
        try (OutputStream out = new FileOutputStream(marker)) {
            out.write("build-264".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
    }

    private void startPythonServer() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                prepareFfmpegRuntime();
                Python.getInstance().getModule("dashboard");
            } catch (PyException e) {
                startupError = "خطأ Python: " + e.getMessage();
                runOnUiThread(() -> showStartupError(startupError));
            } catch (Throwable e) {
                startupError = e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage());
                runOnUiThread(() -> showStartupError("تعذر تشغيل المحرك المدمج: " + startupError));
            }
        }, "TikTokMonitor-Python").start();

        new Thread(() -> {
            long deadline = System.currentTimeMillis() + 30000;
            while (System.currentTimeMillis() < deadline && !isFinishing()) {
                if (startupError != null) return;
                HttpURLConnection connection = null;
                try {
                    connection = (HttpURLConnection) new URL(DASHBOARD_URL).openConnection();
                    connection.setConnectTimeout(800);
                    connection.setReadTimeout(800);
                    connection.setRequestMethod("GET");
                    int code = connection.getResponseCode();
                    if (code >= 200 && code < 500) {
                        runOnUiThread(() -> {
                            if (!isFinishing() && startupError == null) {
                                webView.loadUrl(DASHBOARD_URL);
                            }
                        });
                        return;
                    }
                } catch (Exception ignored) {
                    // Keep waiting while the embedded Python server starts.
                } finally {
                    if (connection != null) connection.disconnect();
                }
                try { Thread.sleep(500); } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
            runOnUiThread(() -> {
                if (!isFinishing() && startupError == null) {
                    showStartupError("لم يبدأ محرك التطبيق خلال 30 ثانية. أغلق التطبيق وافتحه مجددًا؛ إذا تكرر الخطأ فهذه النسخة تحتاج إصلاحًا إضافيًا.");
                }
            });
        }, "TikTokMonitor-StartupCheck").start();
    }

    private void showStartupError(String message) {
        if (isFinishing()) return;
        status.setText(message);
        status.setVisibility(View.VISIBLE);
        webView.setVisibility(View.INVISIBLE);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}

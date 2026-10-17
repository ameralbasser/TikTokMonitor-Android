package com.amer.tiktokmonitor;

import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.view.View;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import com.chaquo.python.PyException;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.net.HttpURLConnection;
import java.net.URL;

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
        webView.setWebChromeClient(new WebChromeClient());
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

    private void startPythonServer() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
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

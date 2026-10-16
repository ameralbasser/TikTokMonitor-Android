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

public class MainActivity extends Activity {
    private WebView webView;
    private TextView status;

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
        webView.setWebViewClient(new WebViewClient());
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(16);
        status.setText("جاري تشغيل مراقب TikTok LIVE...");
        status.setGravity(android.view.Gravity.CENTER);
        status.setBackgroundColor(Color.rgb(11, 13, 18));
        root.addView(status, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        webView.setVisibility(View.INVISIBLE);
        startPythonServer();
        webView.postDelayed(this::openDashboard, 1500);
    }

    private void startPythonServer() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python.getInstance().getModule("dashboard");
            } catch (PyException e) {
                runOnUiThread(() -> status.setText("تعذر تشغيل المحرك: " + e.getMessage()));
            } catch (Throwable e) {
                runOnUiThread(() -> status.setText("تعذر تشغيل التطبيق: "
                    + e.getClass().getSimpleName() + " — " + String.valueOf(e.getMessage())));
            }
        }, "TikTokMonitor-Python").start();
    }

    private void openDashboard() {
        if (isFinishing()) return;
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, android.webkit.WebResourceRequest request,
                    android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) {
                    status.setText("المراقب لا يزال يبدأ التشغيل. أعد المحاولة بعد لحظات.");
                    status.setVisibility(View.VISIBLE);
                    webView.postDelayed(() -> { if (!isFinishing()) webView.reload(); }, 2000);
                }
            }
            @Override
            public void onPageFinished(WebView view, String url) {
                status.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
            }
        });
        webView.loadUrl("http://127.0.0.1:8080");
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}

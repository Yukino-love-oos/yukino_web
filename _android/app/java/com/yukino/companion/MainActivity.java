package com.yukino.companion;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Log;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/** 主界面：一个全屏 WebView 加载现有网页，人格 / 记忆 / 语音逻辑全在服务器。 */
public class MainActivity extends Activity {
    private static final String TAG = "YukinoApp";
    private WebView web;
    private long lastUpdateCheck = 0L;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        AppLog.install(getApplicationContext());

        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // 她的语音要能自动播放（默认 WebView 要求用户手势）
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUserAgentString(s.getUserAgentString() + " YukinoApp/" + Cfg.VERSION_NAME);
        web.setWebViewClient(new WebViewClient());
        web.loadUrl(Cfg.pageUrl());

        requestNotificationPermission();
        startCompanionService();
        askOverlayPermission();
        checkUpdate();
    }

    private void requestNotificationPermission() {
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                if (checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                        != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "notify permission", t);
        }
    }

    private void startCompanionService() {
        try {
            Intent i = new Intent(this, ProactiveService.class);
            if (Build.VERSION.SDK_INT >= 26) {
                startForegroundService(i);
            } else {
                startService(i);
            }
        } catch (Throwable t) {
            Log.w(TAG, "start service failed", t);
        }
    }

    private void askOverlayPermission() {
        try {
            if (Settings.canDrawOverlays(this)) {
                return;
            }
            new AlertDialog.Builder(this)
                    .setTitle("开启悬浮球")
                    .setMessage("需要「显示在其它应用上层」权限，她才能变成随时可点的小球。\n\n现在去开启吗？")
                    .setPositiveButton("去开启", new DialogInterface.OnClickListener() {
                        @Override
                        public void onClick(DialogInterface d, int w) {
                            try {
                                startActivity(new Intent(
                                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                        Uri.parse("package:" + getPackageName())));
                            } catch (Throwable t) {
                                Log.w(TAG, "overlay settings", t);
                            }
                        }
                    })
                    .setNegativeButton("以后", null)
                    .show();
        } catch (Throwable t) {
            Log.w(TAG, "overlay prompt", t);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) {
            web.onResume();
        }
        if (System.currentTimeMillis() - lastUpdateCheck > 3600000L) {
            checkUpdate();
        }
    }

    @Override
    protected void onPause() {
        if (web != null) {
            web.onPause();
        }
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    /** 问服务器有没有新版本（方案 B：应用内检查更新）。 */
    private void checkUpdate() {
        lastUpdateCheck = System.currentTimeMillis();
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection c = null;
                try {
                    c = (HttpURLConnection) new URL(Cfg.api("/api/app/version")).openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(8000);
                    StringBuilder sb = new StringBuilder();
                    BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "utf-8"));
                    String line;
                    while ((line = r.readLine()) != null) {
                        sb.append(line);
                    }
                    r.close();
                    JSONObject o = new JSONObject(sb.toString());
                    final int code = o.optInt("versionCode", 0);
                    if (code <= Cfg.VERSION_CODE) {
                        return;
                    }
                    final String url = o.optString("url", "");
                    final String notes = o.optString("notes", "");
                    final String name = o.optString("versionName", "");
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            new AlertDialog.Builder(MainActivity.this)
                                    .setTitle("有新版本 " + name)
                                    .setMessage(notes.isEmpty() ? "要不要更新？" : notes)
                                    .setPositiveButton("下载", new DialogInterface.OnClickListener() {
                                        @Override
                                        public void onClick(DialogInterface d, int w) {
                                            try {
                                                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                                            } catch (Throwable t) {
                                                Log.w(TAG, "open apk url", t);
                                            }
                                        }
                                    })
                                    .setNegativeButton("稍后", null)
                                    .show();
                        }
                    });
                } catch (Throwable t) {
                    Log.w(TAG, "update check failed", t);
                } finally {
                    if (c != null) {
                        c.disconnect();
                    }
                }
            }
        }, "yukino-update").start();
    }
}

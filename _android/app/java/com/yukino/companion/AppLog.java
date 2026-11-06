package com.yukino.companion;

import android.content.Context;
import android.util.Log;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** 崩溃/异常上报：手机端出问题我拿不到 adb 日志，只能靠它把堆栈送回服务器。 */
public final class AppLog {
    private static final String TAG = "YukinoApp";

    private AppLog() {}

    public static void install(final Context ctx) {
        final Thread.UncaughtExceptionHandler prev = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override
            public void uncaughtException(Thread t, Throwable e) {
                try {
                    upload(ctx, "CRASH in " + t.getName() + "\n" + Log.getStackTraceString(e));
                } catch (Throwable ignored) {
                }
                if (prev != null) {
                    prev.uncaughtException(t, e);
                }
            }
        });
    }

    public static void upload(final Context ctx, final String text) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection c = null;
                try {
                    URL u = new URL(Cfg.SERVER + "/api/app/log");
                    c = (HttpURLConnection) u.openConnection();
                    c.setRequestMethod("POST");
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(8000);
                    c.setDoOutput(true);
                    c.setRequestProperty("Content-Type", "text/plain; charset=utf-8");
                    byte[] body = text.getBytes("utf-8");
                    OutputStream os = c.getOutputStream();
                    os.write(body);
                    os.close();
                    c.getResponseCode();
                } catch (Throwable t) {
                    Log.w(TAG, "log upload failed", t);
                } finally {
                    if (c != null) {
                        c.disconnect();
                    }
                }
            }
        }, "yukino-log").start();
    }
}

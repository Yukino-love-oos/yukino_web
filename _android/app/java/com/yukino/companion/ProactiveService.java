package com.yukino.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.ImageView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 常驻服务：悬浮球 + 主动消息轮询。
 *
 * 为什么用轮询而不是推送：国内 FCM 不可用，第三方推送要接 SDK 且收费。
 * 轮询 30 秒一次，电量代价可接受（约 3~5%/天）。
 */
public class ProactiveService extends Service {
    private static final String TAG = "YukinoApp";
    private static final String CH_FG = "yukino_fg";
    private static final String CH_MSG = "yukino_msg";
    private static final int ID_FG = 1001;

    private volatile boolean running = true;
    private int cursor = 0;
    private boolean inited = false;
    private Thread poller;
    private WindowManager wm;
    private View bubble;
    private WindowManager.LayoutParams bubbleParams;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
        startForeground(ID_FG, buildForeground());
        showBubble();
        poller = new Thread(new Runnable() {
            @Override
            public void run() {
                pollLoop();
            }
        }, "yukino-poll");
        poller.setDaemon(true);
        poller.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        running = false;
        removeBubble();
        super.onDestroy();
    }

    // ---------------- 通知 ----------------
    private void createChannels() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);

        NotificationChannel fg = new NotificationChannel(CH_FG, "常驻", NotificationManager.IMPORTANCE_LOW);
        fg.setDescription("保持悬浮球和主动消息轮询");
        fg.setShowBadge(false);
        nm.createNotificationChannel(fg);

        // IMPORTANCE_HIGH = 横幅弹出（像 QQ 那样从顶部弹出来）
        NotificationChannel msg = new NotificationChannel(CH_MSG, "她的主动消息", NotificationManager.IMPORTANCE_HIGH);
        msg.setDescription("她主动找你说话时弹出");
        msg.enableVibration(true);
        msg.setShowBadge(true);
        nm.createNotificationChannel(msg);
    }

    private PendingIntent mainIntent() {
        Intent i = new Intent(this, MainActivity.class);
        i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        return PendingIntent.getActivity(this, 0, i, flags);
    }

    private Notification buildForeground() {
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, CH_FG);
        } else {
            b = new Notification.Builder(this);
        }
        b.setContentTitle("雪乃在陪着你")
                .setContentText("悬浮球已开启")
                .setSmallIcon(R.drawable.ic_stat)
                .setOngoing(true)
                .setContentIntent(mainIntent());
        return b.build();
    }

    private void notifyMessage(String text) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, CH_MSG);
        } else {
            b = new Notification.Builder(this);
        }
        b.setContentTitle("雪之下雪乃")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setSmallIcon(R.drawable.ic_stat)
                .setAutoCancel(true)
                .setContentIntent(mainIntent())
                .setPriority(Notification.PRIORITY_HIGH);
        int id = 20000 + (int) (System.currentTimeMillis() % 10000);
        nm.notify(id, b.build());
    }

    // ---------------- 轮询 ----------------
    private void pollLoop() {
        while (running) {
            HttpURLConnection c = null;
            try {
                String q = "/api/app/proactive?since=" + cursor + (inited ? "" : "&init=1");
                c = (HttpURLConnection) new URL(Cfg.api(q)).openConnection();
                c.setConnectTimeout(10000);
                c.setReadTimeout(10000);
                StringBuilder sb = new StringBuilder();
                BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "utf-8"));
                String line;
                while ((line = r.readLine()) != null) {
                    sb.append(line);
                }
                r.close();
                JSONObject o = new JSONObject(sb.toString());
                cursor = o.optInt("cursor", cursor);
                inited = true;
                JSONArray arr = o.optJSONArray("messages");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject m = arr.getJSONObject(i);
                        String text = m.optString("text", "");
                        if (text.length() > 0) {
                            notifyMessage(text);
                        }
                    }
                }
            } catch (Throwable t) {
                Log.w(TAG, "poll failed", t);
            } finally {
                if (c != null) {
                    c.disconnect();
                }
            }
            try {
                Thread.sleep(Cfg.POLL_SECONDS * 1000L);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    // ---------------- 悬浮球 ----------------
    private int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void showBubble() {
        try {
            if (!Settings.canDrawOverlays(this)) {
                Log.w(TAG, "no overlay permission, bubble skipped");
                return;
            }
            wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);
            ImageView iv = new ImageView(this);
            iv.setImageResource(R.mipmap.ic_launcher);
            iv.setBackgroundResource(R.drawable.bubble_bg);
            int size = dp(56);
            iv.setLayoutParams(new android.widget.FrameLayout.LayoutParams(size, size));
            iv.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
            iv.setPadding(dp(6), dp(6), dp(6), dp(6));

            int type = Build.VERSION.SDK_INT >= 26
                    ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                    : WindowManager.LayoutParams.TYPE_PHONE;
            bubbleParams = new WindowManager.LayoutParams(
                    size, size, type,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                    PixelFormat.TRANSLUCENT);
            bubbleParams.gravity = Gravity.TOP | Gravity.START;
            bubbleParams.x = getResources().getDisplayMetrics().widthPixels - dp(80);
            bubbleParams.y = getResources().getDisplayMetrics().heightPixels / 2;

            final long[] down = new long[2];
            final int[] start = new int[2];
            final boolean[] moved = new boolean[1];
            iv.setOnTouchListener(new View.OnTouchListener() {
                @Override
                public boolean onTouch(View v, MotionEvent e) {
                    switch (e.getActionMasked()) {
                        case MotionEvent.ACTION_DOWN:
                            down[0] = (long) e.getRawX();
                            down[1] = (long) e.getRawY();
                            start[0] = bubbleParams.x;
                            start[1] = bubbleParams.y;
                            moved[0] = false;
                            return true;
                        case MotionEvent.ACTION_MOVE:
                            int dx = (int) (e.getRawX() - down[0]);
                            int dy = (int) (e.getRawY() - down[1]);
                            if (Math.abs(dx) > dp(6) || Math.abs(dy) > dp(6)) {
                                moved[0] = true;
                            }
                            bubbleParams.x = start[0] + dx;
                            bubbleParams.y = start[1] + dy;
                            try {
                                wm.updateViewLayout(bubble, bubbleParams);
                            } catch (Throwable ignored) {
                            }
                            return true;
                        case MotionEvent.ACTION_UP:
                            if (!moved[0]) {
                                try {
                                    Intent i = new Intent(ProactiveService.this, MainActivity.class);
                                    i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                                    startActivity(i);
                                } catch (Throwable t) {
                                    Log.w(TAG, "open from bubble", t);
                                }
                            }
                            return true;
                        default:
                            return false;
                    }
                }
            });

            bubble = iv;
            wm.addView(bubble, bubbleParams);
            Log.i(TAG, "bubble shown");
        } catch (Throwable t) {
            Log.w(TAG, "bubble failed", t);
        }
    }

    private void removeBubble() {
        try {
            if (wm != null && bubble != null) {
                wm.removeView(bubble);
            }
        } catch (Throwable ignored) {
        }
        bubble = null;
    }
}

package com.yukino.companion;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/** 开机自启（尽量把悬浮球拉起来，HyperOS 上可能被系统拦，失败就算了）。 */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        if (i == null || !Intent.ACTION_BOOT_COMPLETED.equals(i.getAction())) {
            return;
        }
        try {
            Intent s = new Intent(c, ProactiveService.class);
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                c.startForegroundService(s);
            } else {
                c.startService(s);
            }
        } catch (Throwable t) {
            Log.w("YukinoApp", "boot start failed", t);
        }
    }
}

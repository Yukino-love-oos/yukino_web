package com.yukino.companion;

/** 单用户配置：服务器地址 / 访问令牌 / 版本号（改版本时只动这里）。 */
public final class Cfg {
    public static final String SERVER = "http://42.120.61.220:8501";
    public static final String TOKEN = "br0i2cwopa";
    public static final int VERSION_CODE = 1;
    public static final String VERSION_NAME = "1.0";
    public static final int POLL_SECONDS = 30;

    private Cfg() {}

    /** 聊天页地址（首次带 token，之后靠 cookie）。 */
    public static String pageUrl() {
        return SERVER + "/?t=" + TOKEN;
    }

    /** 接口地址（统一附带 token）。 */
    public static String api(String path) {
        return SERVER + path + (path.contains("?") ? "&" : "?") + "t=" + TOKEN;
    }
}

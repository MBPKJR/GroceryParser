package com.strehler.zuhause.core;

import android.util.Log;

public class Logger {
    public static void d(String tag, String message) {
        Log.d("HAZuhause_" + tag, message);
    }
}

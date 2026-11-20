package app.gamenative.externaldisplay

import android.content.Context
import android.os.Build
import android.view.Display
import android.view.WindowManager

@Suppress("DEPRECATION")
internal fun Context.currentDisplayCompat(): Display? =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        display
    } else {
        getSystemService(WindowManager::class.java)?.defaultDisplay
    }

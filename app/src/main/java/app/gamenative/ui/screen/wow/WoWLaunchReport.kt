package app.gamenative.ui.screen.wow

import android.os.Build
import android.os.SystemClock
import app.gamenative.BuildConfig

/** Holds fixed startup observations for the latest debug attempt. Never stores guest output. */
internal class WoWLaunchReport private constructor(
    private val driver: String,
    private val display: String,
    private val resolution: String,
) {
    private val startedAt = SystemClock.elapsedRealtime()
    private val events = linkedMapOf<String, Long>()

    @Synchronized
    fun record(event: String) {
        if (event !in events) events[event] = (SystemClock.elapsedRealtime() - startedAt) / 1000
    }

    fun observeGuestOutput(line: String) {
        val event = when {
            line.startsWith("CANNOT LINK EXECUTABLE") -> "Android linker rejected a guest executable"
            line.contains(":err:module:import_dll") -> "Wine reported a DLL import error"
            line.contains(":err:module:LdrInitializeThunk") -> "Wine failed to initialize an executable"
            line.contains(":err:vulkan:") -> "Wine reported a Vulkan error"
            line.contains(":err:winediag:") -> "Wine reported a runtime error"
            else -> return
        }
        record(event)
    }

    @Synchronized
    fun summary(): String = buildString {
        appendLine("${Build.MANUFACTURER} ${Build.MODEL}, Android API ${Build.VERSION.SDK_INT}")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) appendLine("SoC: ${Build.SOC_MODEL}")
        appendLine("Driver: $driver")
        appendLine("Display: $display at $resolution")
        for ((event, seconds) in events) appendLine("${seconds}s  $event")
    }.trimEnd()

    companion object {
        @Volatile
        var latest: WoWLaunchReport? = null
            private set

        fun begin(driver: String, display: String, resolution: String): WoWLaunchReport? {
            if (!BuildConfig.DEBUG) return null
            return WoWLaunchReport(driver, display, resolution).also { latest = it }
        }
    }
}

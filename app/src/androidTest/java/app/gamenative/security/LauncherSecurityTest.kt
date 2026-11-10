package app.gamenative.security

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.security.NetworkSecurityPolicy
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.gamenative.ui.screen.wow.BattleNetSignIn
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LauncherSecurityTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Suppress("DEPRECATION")
    @Test fun mergedManifestDeniesCleartextAndMicrophone() {
        val pm = context.packageManager
        val permissions = pm.getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toSet()
        assertFalse(Manifest.permission.RECORD_AUDIO in permissions)
        assertTrue(Manifest.permission.INTERNET in permissions)
        assertFalse(NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted)
    }

    @Suppress("DEPRECATION")
    @Test fun onlyTheLauncherAliasIsAnExportedActivity() {
        val exported = context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_ACTIVITIES)
            .activities.orEmpty().filter { it.exported }.map { it.name }.toSet()
        assertEquals(setOf("app.gamenative.MainActivityAliasDefault"), exported)
    }

    @Test fun legacyPreferencesAreDeletedWithoutReading() {
        val prefs = context.getSharedPreferences("battle_net_login", Context.MODE_PRIVATE)
        assertTrue(prefs.edit().putString("password", "not-a-real-secret").commit())
        BattleNetSignIn.removeSavedLogin(context)
        assertFalse(context.getSharedPreferences("battle_net_login", Context.MODE_PRIVATE).contains("password"))
    }
}

package app.gamenative.utils

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.Assert.assertThrows
import java.io.IOException

class AppUpdaterTest {

    @Test
    fun isNewer_higherPatch_returnsTrue() {
        assertTrue(AppUpdater.isNewer("v2.1.3", "2.1.2"))
    }

    @Test
    fun isNewer_higherMinor_returnsTrue() {
        assertTrue(AppUpdater.isNewer("2.2.0", "2.1.2"))
    }

    @Test
    fun isNewer_higherMajor_returnsTrue() {
        assertTrue(AppUpdater.isNewer("v3.0.0", "2.1.2"))
    }

    @Test
    fun isNewer_sameVersion_returnsFalse() {
        assertFalse(AppUpdater.isNewer("v2.1.2", "2.1.2"))
        assertFalse(AppUpdater.isNewer("2.1.2", "2.1.2"))
    }

    @Test
    fun isNewer_olderVersion_returnsFalse() {
        assertFalse(AppUpdater.isNewer("v2.1.1", "2.1.2"))
        assertFalse(AppUpdater.isNewer("1.9.9", "2.1.2"))
    }

    @Test
    fun isNewer_extraSegments_comparesCorrectly() {
        assertTrue(AppUpdater.isNewer("v2.1.2.1", "2.1.2"))
        assertFalse(AppUpdater.isNewer("2.1", "2.1.2"))
    }

    @Test
    fun isNewer_handlesPrereleaseSuffix() {
        assertFalse(AppUpdater.isNewer("v2.1.2-beta1", "2.1.2"))
        assertTrue(AppUpdater.isNewer("v2.1.3-rc1", "2.1.2"))
    }

    @Test
    fun parseRelease_validNewReleaseWithApk_parsesSuccessfully() {
        val json = """
            {
                "tag_name": "v2.1.3",
                "name": "WoW Forever v2.1.3",
                "body": "Bug fixes and improvements",
                "assets": [
                    {
                        "name": "WoW-Forever.apk",
                        "browser_download_url": "https://github.com/Lanceroyy/wow-forever-android/releases/download/v2.1.3/WoW-Forever.apk",
                        "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "size": 160000000
                    }
                ]
            }
        """.trimIndent()

        val release = AppUpdater.parseRelease(json, "2.1.2")

        assertNotNull(release)
        assertEquals("2.1.3", release?.version)
        assertEquals("WoW Forever v2.1.3", release?.name)
        assertEquals("Bug fixes and improvements", release?.notes)
        assertEquals("https://github.com/Lanceroyy/wow-forever-android/releases/download/v2.1.3/WoW-Forever.apk", release?.downloadUrl)
        assertEquals(160000000L, release?.sizeBytes)
    }

    @Test
    fun parseRelease_sameOrOlderVersion_returnsNull() {
        val json = """
            {
                "tag_name": "v2.1.2",
                "name": "WoW Forever v2.1.2",
                "body": "Existing release",
                "assets": [
                    {
                        "name": "WoW-Forever.apk",
                        "browser_download_url": "https://github.com/Lanceroyy/wow-forever-android/releases/download/v2.1.2/WoW-Forever.apk",
                        "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "size": 160000000
                    }
                ]
            }
        """.trimIndent()

        assertNull(AppUpdater.parseRelease(json, "2.1.2"))
    }

    @Test
    fun parseRelease_noApkAsset_returnsNull() {
        val json = """
            {
                "tag_name": "v2.1.3",
                "name": "WoW Forever v2.1.3",
                "body": "Source only release",
                "assets": [
                    {
                        "name": "source.tar.gz",
                        "browser_download_url": "https://example.com/source.tar.gz",
                        "size": 5000
                    }
                ]
            }
        """.trimIndent()

        assertNull(AppUpdater.parseRelease(json, "2.1.2"))
    }

    @Test
    fun parseRelease_invalidJson_returnsNull() {
        assertNull(AppUpdater.parseRelease("not-json", "2.1.2"))
    }

    @Test fun requiresDigestAndExactRepoAssetPath() {
        val url = "https://github.com/${AppUpdater.REPOSITORY}/releases/download/v2.1.3/WoW.apk"
        fun release(download: String, digest: String?) = AppUpdater.parseRelease(
            org.json.JSONObject().put("tag_name", "v2.1.3").put("assets", org.json.JSONArray().put(
                org.json.JSONObject().put("name", "WoW.apk").put("size", 12)
                    .put("browser_download_url", download).put("digest", digest),
            )).toString(),
            "2.1.2",
        )
        val goodDigest = "sha256:" + "0".repeat(64)
        assertNotNull(release(url, goodDigest))
        assertNull(release(url, null))
        assertNull(release(url, "sha256:abcd"))
        for (invalid in listOf(
            url.replace("https:", "http:"),
            url.replace("github.com", "github.com.example"),
            url.replace("Lanceroyy", "jaredgei"),
            url.replace("WoW.apk", "../WoW.apk"),
            "$url?other=1",
        )) assertNull(release(invalid, goodDigest))
    }

    @Test fun allowsOnlyObservedHttpsAssetRedirect() {
        val url = "https://release-assets.githubusercontent.com/github-production-release-asset/123/f4a43be0-d5c9-4c3d-b5c2-dd45d6371ad5"
        assertNotNull(AppUpdater.trustedAssetRedirect("$url?signature=abc"))
        for (invalid in listOf(
            url.replace("https:", "http:"),
            url.replace(".com/", ".com.example/"),
            url.replace(".com/", ".com:444/"),
            url.replace(".com/", ".com/../"),
            url.replace("github-production-release-asset", "unexpected"),
        )) assertNull(AppUpdater.trustedAssetRedirect(invalid))
    }

    @Test fun rejectsSameSizeModifiedApk() {
        val file = kotlin.io.path.createTempFile().toFile()
        try {
            val sha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            file.writeText("abc")
            AppUpdater.verifyDigest(file, 3, sha256)
            file.writeText("abd")
            assertThrows(IOException::class.java) { AppUpdater.verifyDigest(file, 3, sha256) }
            file.writeText("ab")
            assertThrows(IOException::class.java) { AppUpdater.verifyDigest(file, 3, sha256) }
        } finally {
            file.delete()
        }
    }

    @Test fun rejectsIncorrectSignerPackageAndDowngrade() {
        val installed = AppUpdater.ApkIdentity("app.wowforever", 233, "2.3.3", setOf("test signer A"))
        val update = installed.copy(versionCode = 234, versionName = "2.3.4")
        AppUpdater.verifyIdentity(installed, update, "2.3.4")
        for (invalid in listOf(
            update.copy(signers = setOf("test signer B")),
            update.copy(signers = emptySet()),
            update.copy(packageName = "other.package"),
            update.copy(versionCode = 233),
            update.copy(versionName = "2.3.5"),
        )) assertThrows(IOException::class.java) { AppUpdater.verifyIdentity(installed, invalid, "2.3.4") }
    }
}

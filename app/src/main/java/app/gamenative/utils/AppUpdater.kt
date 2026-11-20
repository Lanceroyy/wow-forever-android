package app.gamenative.utils

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.os.Build
import androidx.core.content.FileProvider
import app.gamenative.BuildConfig
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardCopyOption.REPLACE_EXISTING
import java.security.MessageDigest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import timber.log.Timber

object AppUpdater {
    internal const val REPOSITORY = "Lanceroyy/wow-forever-android"
    private const val RELEASES_URL = "https://api.github.com/repos/$REPOSITORY/releases/latest"
    private const val MAX_APK_BYTES = 1024L * 1024 * 1024
    private val tagFormat = Regex("[vV]?[0-9]{1,9}(?:[.][0-9]{1,9}){1,3}")
    private val nameFormat = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,119}[.]apk", RegexOption.IGNORE_CASE)
    private val digestFormat = Regex("sha256:[a-fA-F0-9]{64}")
    private val redirectPath = Regex("/github-production-release-asset(?:-[0-9a-f]+)?/[0-9]+/[a-fA-F0-9-]{36}")

    @ConsistentCopyVisibility
    data class Release internal constructor(
        val version: String,
        val name: String,
        val notes: String,
        val downloadUrl: String,
        val sizeBytes: Long,
        internal val sha256: String,
        internal val tag: String,
        internal val assetName: String,
    )

    class VerifiedUpdate internal constructor(internal val file: File, internal val release: Release) {
        fun exists(): Boolean = file.isFile
    }

    fun isNewer(latest: String, current: String = BuildConfig.VERSION_NAME): Boolean {
        val parse = { v: String ->
            v.trimStart('v', 'V')
                .split('-', '_', '+')[0]
                .split('.')
                .mapNotNull { it.toIntOrNull() }
        }
        val l = parse(latest)
        val c = parse(current)
        for (i in 0 until maxOf(l.size, c.size)) {
            val lv = l.getOrElse(i) { 0 }
            val cv = c.getOrElse(i) { 0 }
            if (lv > cv) return true
            if (lv < cv) return false
        }
        return false
    }

    fun parseRelease(jsonString: String, currentVersion: String = BuildConfig.VERSION_NAME): Release? = runCatching {
        val json = JSONObject(jsonString)
        val tag = json.optString("tag_name")
        if (!tagFormat.matches(tag) || !isNewer(tag, currentVersion) ||
            json.optBoolean("draft") || json.optBoolean("prerelease")
        ) return null

        val assets = json.optJSONArray("assets") ?: return null
        for (i in 0 until assets.length()) {
            val asset = assets.optJSONObject(i) ?: continue
            val name = asset.optString("name")
            val url = asset.optString("browser_download_url")
            val digest = asset.optString("digest")
            val size = asset.optLong("size", 0L)
            if (trustedReleaseUrl(url, tag, name) && digestFormat.matches(digest) && size in 1L..MAX_APK_BYTES) {
                return Release(
                    version = tag.trimStart('v', 'V'),
                    name = json.optString("name", tag),
                    notes = json.optString("body"),
                    downloadUrl = url,
                    sizeBytes = size,
                    sha256 = digest.substringAfter(':').lowercase(),
                    tag = tag,
                    assetName = name,
                )
            }
        }
        null
    }.getOrNull()

    suspend fun check(currentVersion: String = BuildConfig.VERSION_NAME): Release? = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url(RELEASES_URL)
                .header("User-Agent", "WoW-Forever-Android")
                .header("Accept", "application/vnd.github+json")
                .build()
            Net.strictHttp.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val responseBody = response.body ?: return@withContext null
                if (responseBody.source().request(1024 * 1024L + 1)) return@withContext null
                val body = responseBody.string()
                parseRelease(body, currentVersion)
            }
        }.getOrElse {
            Timber.w(it, "AppUpdater check failed")
            null
        }
    }

    internal fun trustedReleaseUrl(url: String, tag: String, name: String): Boolean =
        tagFormat.matches(tag) && nameFormat.matches(name) &&
            url == "https://github.com/$REPOSITORY/releases/download/$tag/$name"

    internal fun trustedAssetRedirect(raw: String): HttpUrl? {
        if (!raw.startsWith("https://release-assets.githubusercontent.com/") ||
            raw.any { it.isWhitespace() || it == '\\' } || '#' in raw
        ) return null
        val url = raw.toHttpUrlOrNull() ?: return null
        if (url.host != "release-assets.githubusercontent.com" || url.port != 443 ||
            url.username.isNotEmpty() || url.password.isNotEmpty() || !redirectPath.matches(url.encodedPath) ||
            raw.substringBefore('?') != "https://release-assets.githubusercontent.com${url.encodedPath}"
        ) return null
        return url
    }

    private fun requestAsset(release: Release): Response {
        val request = Request.Builder().url(release.downloadUrl).header("Accept-Encoding", "identity").build()
        val first = Net.strictHttp.newCall(request).execute()
        if (first.code !in setOf(301, 302, 303, 307, 308)) return first
        val redirect = first.use { trustedAssetRedirect(it.header("Location").orEmpty()) }
            ?: throw IOException("Untrusted APK download redirect")
        // A signed GitHub CDN URL may be followed once. The second response must be HTTP 200.
        return Net.strictHttp.newCall(request.newBuilder().url(redirect).build()).execute()
    }

    suspend fun download(
        context: Context,
        release: Release,
        onProgress: (Float) -> Unit,
    ): VerifiedUpdate = withContext(Dispatchers.IO) {
        if (!trustedReleaseUrl(release.downloadUrl, release.tag, release.assetName) ||
            !digestFormat.matches("sha256:${release.sha256}") || release.sizeBytes !in 1L..MAX_APK_BYTES
        ) throw IOException("Invalid APK release metadata")
        val dir = File(context.cacheDir, "updates")
        if (!dir.isDirectory && !dir.mkdirs()) throw IOException("Could not create update cache")
        val dest = File(dir, "${release.sha256}.apk")
        if (dest.isFile) {
            try {
                verifyApk(context, dest, release)
                onProgress(1f)
                return@withContext VerifiedUpdate(dest, release)
            } catch (_: IOException) {
                Files.deleteIfExists(dest.toPath())
            }
        }
        val temporary = File.createTempFile(".update-", ".apk", dir)
        try {
            requestAsset(release).use { response ->
                if (response.code != 200) throw IOException("Unexpected APK download response: ${response.code}")
                val body = response.body ?: throw IOException("Empty APK download")
                if (body.contentLength() >= 0 && body.contentLength() != release.sizeBytes) {
                    throw IOException("APK download length mismatch")
                }
                temporary.outputStream().buffered().use { out ->
                    val input = body.byteStream()
                    val buffer = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        ensureActive()
                        val n = input.read(buffer)
                        if (n < 0) break
                        total += n
                        if (total > release.sizeBytes) throw IOException("APK download exceeds expected size")
                        out.write(buffer, 0, n)
                        onProgress(total.toFloat() / release.sizeBytes)
                    }
                }
            }
            verifyApk(context, temporary, release)
            Files.move(temporary.toPath(), dest.toPath(), ATOMIC_MOVE, REPLACE_EXISTING)
            VerifiedUpdate(dest, release)
        } catch (e: CancellationException) {
            throw e
        } finally {
            Files.deleteIfExists(temporary.toPath())
        }
    }

    internal fun verifyDigest(file: File, expectedBytes: Long, expectedHash: String) {
        val md = MessageDigest.getInstance("SHA-256")
        var size = 0L
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                size += count
                if (size > expectedBytes) throw IOException("APK download length mismatch")
                md.update(buffer, 0, count)
            }
        }
        val actual = md.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        if (size != expectedBytes || actual != expectedHash) throw IOException("APK SHA-256 verification failed")
    }

    internal data class ApkIdentity(val packageName: String, val versionCode: Long, val versionName: String?, val signers: Set<String>)

    internal fun verifyIdentity(installed: ApkIdentity, candidate: ApkIdentity, expectedVersion: String) {
        if (candidate.packageName != installed.packageName || candidate.versionCode <= installed.versionCode ||
            candidate.versionName != expectedVersion
        ) throw IOException("APK package or version does not match the update")
        if (installed.signers.isEmpty() || installed.signers != candidate.signers) {
            throw IOException("APK signer does not match the installed app")
        }
    }

    @Suppress("DEPRECATION")
    private fun verifyApk(context: Context, apk: File, release: Release) {
        verifyDigest(apk, release.sizeBytes, release.sha256)
        val pm = context.packageManager
        val modern = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
        val flags = if (modern) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        fun identity(info: PackageInfo): ApkIdentity {
            val versionCode = if (modern) info.longVersionCode else info.versionCode.toLong()
            val signers = if (modern) info.signingInfo?.apkContentsSigners else info.signatures
            return ApkIdentity(info.packageName, versionCode, info.versionName, signers?.map { it.toCharsString() }?.toSet().orEmpty())
        }
        val installed = pm.getPackageInfo(context.packageName, flags)
        val candidate = pm.getPackageArchiveInfo(apk.absolutePath, flags) ?: throw IOException("Invalid update APK")
        verifyIdentity(identity(installed), identity(candidate), release.version)
    }

    fun canInstall(context: Context): Boolean = runCatching {
        context.packageManager.canRequestPackageInstalls()
    }.getOrElse {
        Timber.w(it, "canRequestPackageInstalls check failed")
        false
    }

    fun openInstallPermissionSettings(context: Context) {
        runCatching {
            context.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                    data = Uri.parse("package:${context.packageName}")
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                },
            )
        }.onFailure {
            runCatching {
                context.startActivity(
                    Intent(Settings.ACTION_SECURITY_SETTINGS).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    },
                )
            }
        }
    }

    suspend fun install(context: Context, update: VerifiedUpdate) {
        withContext(Dispatchers.IO) { verifyApk(context, update.file, update.release) }
        if (!canInstall(context)) {
            openInstallPermissionSettings(context)
            return
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", update.file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }
}

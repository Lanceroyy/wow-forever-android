package app.gamenative.utils

import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardCopyOption.REPLACE_EXISTING
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import timber.log.Timber

object Net {
    val http: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .callTimeout(5, TimeUnit.MINUTES)
            .pingInterval(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    internal fun httpsWithoutRedirects(client: OkHttpClient): OkHttpClient =
        client.newBuilder()
            .followRedirects(false)
            .followSslRedirects(false)
            .addInterceptor { chain ->
                if (!chain.request().url.isHttps) throw IOException("HTTPS is required")
                chain.proceed(chain.request())
            }
            .build()

    internal val strictHttp: OkHttpClient by lazy { httpsWithoutRedirects(http) }

    private val downloadHttp: OkHttpClient by lazy {
        strictHttp.newBuilder()
            .readTimeout(5, TimeUnit.MINUTES)
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .protocols(listOf(Protocol.HTTP_1_1))
            .build()
    }

    private suspend fun fetchFile(
        url: String,
        dest: File,
        onProgress: (Float) -> Unit,
    ) = withContext(Dispatchers.IO) {
        val tmp = File.createTempFile(".component-", ".part", dest.parentFile)
        try {
            downloadHttp.newCall(Request.Builder().url(url).build()).execute().use { rsp ->
                check(rsp.code == 200) { "HTTP ${rsp.code}" }
                val body = rsp.body ?: error("empty body")
                val total = body.contentLength()
                tmp.outputStream().use { out ->
                    val input = body.byteStream()
                    val buf = ByteArray(8 * 1024)
                    var read = 0L
                    while (true) {
                        ensureActive()
                        val n = input.read(buf)
                        if (n < 0) break
                        if (n == 0) continue
                        out.write(buf, 0, n)
                        read += n
                        if (total > 0) onProgress(read.toFloat() / total)
                    }
                }
                if (total >= 0 && tmp.length() != total) throw IOException("Incomplete component download")
                Files.move(tmp.toPath(), dest.toPath(), ATOMIC_MOVE, REPLACE_EXISTING)
            }
        } finally {
            Files.deleteIfExists(tmp.toPath())
        }
    }

    private val componentPath = Regex(
        "(?:container_files|core_drivers|dxwrapper|graphics_driver|wincomponents)/[A-Za-z0-9][A-Za-z0-9._-]*[.](?:tzst|zip|tar[.]xz)",
    )

    internal fun isRuntimeComponentPath(path: String): Boolean =
        path in setOf("imagefs_bionic.txz", "imagefs_gamenative.txz") || componentPath.matches(path)

    suspend fun fetchFileWithFallback(
        fileName: String,
        dest: File,
        onProgress: (Float) -> Unit,
    ) = withContext(Dispatchers.IO) {
        require(isRuntimeComponentPath(fileName)) { "Invalid GameNative component path" }
        try {
            fetchFile("https://downloads.gamenative.app/$fileName", dest, onProgress)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.w(e, "Primary download failed; retrying with fallback URL")
            try {
                fetchFile("https://pub-9fcd5294bd0d4b85a9d73615bf98f3b5.r2.dev/$fileName", dest, onProgress)
            } catch (e2: CancellationException) {
                throw e2
            } catch (e2: Exception) {
                throw IOException(
                    "Failed to download $fileName. Please check your network connection or try a VPN.",
                    e2,
                )
            }
        }
    }
}

package app.gamenative.ui.screen.wow

import app.gamenative.utils.Net
import app.gamenative.ui.screen.wow.WowDownloadPolicy.hex
import java.nio.file.Files
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.nio.ByteBuffer
import java.security.DigestOutputStream
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.zip.Inflater
import timber.log.Timber

object WowClientDownloader {
    const val FLAVOR_DIR = "_forever_"
    const val EXE_NAME = "Wow-ARM64.exe"
    const val TARGET_PRODUCT = "wowf"
    const val BUILD_INFO = ".build.info"
    internal const val UPDATE_MARKER = ".wow-update-in-progress"

    // The pre-release beta. Its Data/ archives are shared with the full release, so beta installs upgrade in place.
    const val LEGACY_FLAVOR_DIR = "_classic_beta_"
    const val LEGACY_PRODUCT = "wow_classic_beta"
    private const val PLATFORM_ARCH = "arm64"
    private const val EXCLUDED_ARCH = "x86_64"
    private const val PATCH_URL = "https://us.version.battle.net/v2/products/$TARGET_PRODUCT"
    private const val DEFAULT_CDN_PATH = WowDownloadPolicy.CDN_PATH
    private const val MAX_OBJECT = 160 * 1024 * 1024
    private const val MAX_CHUNK = 32 * 1024 * 1024
    private const val INDEX_DOWNLOAD_THREADS = 8
    private val BUILD_INFO_HEADER = listOf(
        "Branch!STRING:0", "Active!DEC:1", "Build Key!HEX:16", "CDN Key!HEX:16", "Install Key!HEX:16", "IM Size!DEC:4",
        "CDN Path!STRING:0", "CDN Hosts!STRING:0", "CDN Servers!STRING:0", "Tags!STRING:0", "Armadillo!STRING:0",
        "Last Activated!STRING:0", "Version!STRING:0", "KeyRing!HEX:16", "Product!STRING:0",
    ).joinToString("|")
    private val EU_COUNTRIES = setOf(
        "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT",
        "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "RU", "SE", "SI", "SK", "UA", "TR",
    )

    private val patchHttp by lazy {
        Net.strictHttp.newBuilder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(5, TimeUnit.SECONDS)
            .callTimeout(6, TimeUnit.SECONDS)
            .build()
    }

    data class VersionCheckResult(
        val localVersion: String,
        val remoteVersion: String,
        val isOutdated: Boolean,
        val remoteBuildConfig: String = "",
        val remoteCdnConfig: String = "",
        val remoteCdnHosts: List<String> = emptyList(),
        val remoteCdnPath: String = DEFAULT_CDN_PATH,
        val remoteCdnServers: List<String> = emptyList(),
    )

    fun readBuildInfo(buildInfo: File): Map<String, String>? =
        runCatching { activeBuild(buildInfo) }.getOrNull()

    fun installedProducts(buildInfo: File): Set<String> =
        runCatching { parsePsv(buildInfo.readText()).mapNotNull { it["Product"] }.toSet() }.getOrDefault(emptySet())

    fun checkVersion(gameRoot: File): VersionCheckResult? {
        val row = runCatching { activeBuild(File(gameRoot, BUILD_INFO)) }.getOrElse {
            Timber.w(it, "checkVersion: no readable $BUILD_INFO in $gameRoot")
            return null
        }
        val product = row["Product"]?.takeIf { it.isNotBlank() } ?: TARGET_PRODUCT
        if (product != TARGET_PRODUCT) {
            Timber.w("checkVersion: wrong product '$product', expected '$TARGET_PRODUCT'")
            return null
        }
        val localVersion = row["Version"] ?: run {
            Timber.w("checkVersion: row has no Version")
            return null
        }
        val localBuildKey = row["Build Key"] ?: run {
            Timber.w("checkVersion: row has no Build Key")
            return null
        }

        val remote = fetchRemote() ?: return null
        val isOutdated = localVersion != remote.remoteVersion ||
            (remote.remoteBuildConfig.isNotBlank() && localBuildKey != remote.remoteBuildConfig)
        Timber.i("checkVersion: localVersion=$localVersion, remoteVersion=${remote.remoteVersion}, isOutdated=$isOutdated")
        return remote.copy(localVersion = localVersion, isOutdated = isOutdated)
    }

    private fun fetchRemote(): VersionCheckResult? = try {
        parseRemote(fetchText("$PATCH_URL/versions"), fetchText("$PATCH_URL/cdns"))
    } catch (e: IOException) {
        Timber.w(e, "Blizzard HTTPS metadata unavailable or invalid")
        null
    }

    internal fun parseRemote(versions: String, cdns: String): VersionCheckResult {
        val version = usRow(versions, "Region")
        val remoteVersion = version["VersionsName"]?.takeIf { Regex("[0-9]+(?:[.][0-9]+){1,5}").matches(it) }
            ?: throw IOException("Invalid Blizzard build version")
        val servers = httpsServers(cdns)
        return VersionCheckResult(
            localVersion = "",
            remoteVersion = remoteVersion,
            isOutdated = true,
            remoteBuildConfig = WowDownloadPolicy.key(version["BuildConfig"].orEmpty()),
            remoteCdnConfig = WowDownloadPolicy.key(version["CDNConfig"].orEmpty()),
            remoteCdnHosts = servers.mapNotNull(WowDownloadPolicy::cdnHost).distinct(),
            remoteCdnPath = DEFAULT_CDN_PATH,
            remoteCdnServers = servers,
        )
    }

    private fun usRow(text: String, column: String): Map<String, String> =
        parsePsv(text, strict = true).singleOrNull { it[column] == "us" }
            ?: throw IOException("Invalid Blizzard $column metadata")

    private fun httpsServers(cdns: String): List<String> {
        val cdn = usRow(cdns, "Name")
        if (cdn["Path"] != DEFAULT_CDN_PATH) throw IOException("Untrusted Blizzard CDN path")
        // TACT v2 advertises legacy HTTP routes too. Only the known, official HTTPS entries are usable.
        val servers = splitHosts(cdn["Servers"].orEmpty()).filter { WowDownloadPolicy.cdnHost(it) != null }.distinct()
        if (servers.isEmpty()) throw IOException("Blizzard did not advertise a trusted HTTPS CDN")
        return servers
    }

    /** Old and desktop installs may still advertise HTTP routes to the native guest. */
    fun prepareGuestCdnRoutes(gameRoot: File) {
        val info = WowDownloadPolicy.destination(gameRoot, BUILD_INFO)
        val current = activeBuild(info)
        val servers = splitHosts(current["CDN Servers"].orEmpty())
        val allowed = servers.filter { WowDownloadPolicy.cdnHost(it) != null }
        val hosts = allowed.mapNotNull(WowDownloadPolicy::cdnHost)
        if (current["CDN Path"] == DEFAULT_CDN_PATH && hosts.isNotEmpty() && hosts.size == servers.size &&
            splitHosts(current["CDN Hosts"].orEmpty()) == hosts.distinct()
        ) return
        rewriteGuestCdnRoutes(info, allowed.ifEmpty { httpsServers(fetchText("$PATCH_URL/cdns")) })
    }

    internal fun rewriteGuestCdnRoutes(buildInfo: File, servers: List<String>) {
        val hosts = servers.map { WowDownloadPolicy.cdnHost(it) ?: throw IOException("Untrusted Blizzard CDN route") }
        if (hosts.isEmpty()) throw IOException("No trusted Blizzard CDN")
        val lines = buildInfo.readLines().toMutableList()
        val header = psvHeader(lines.firstOrNull() ?: throw IOException("Missing Blizzard build header"))
        val values = mapOf("CDN Path" to DEFAULT_CDN_PATH, "CDN Hosts" to hosts.distinct().joinToString(" "),
            "CDN Servers" to servers.joinToString(" "))
        if (!header.containsAll(values.keys) || "Product" !in header) throw IOException("Missing Blizzard CDN columns")
        val rows = lines.indices.drop(1).filter { i ->
            lines[i].split("|").getOrNull(header.indexOf("Product")) == TARGET_PRODUCT
        }
        val index = rows.firstOrNull { lines[it].split("|").getOrNull(header.indexOf("Active")) == "1" }
            ?: rows.firstOrNull() ?: throw IOException("Missing WoW Forever build row")
        val cells = lines[index].split("|").toMutableList()
        if (cells.size < header.size) throw IOException("Incomplete WoW Forever build row")
        values.forEach { (name, value) -> cells[header.indexOf(name)] = value }
        lines[index] = cells.joinToString("|")
        writeAtomically(buildInfo, (lines.joinToString("\n") + "\n").toByteArray())
    }

    /**
     * Sets up the full-release client in [gameRoot]: an empty folder for a new install, or a beta install that is upgraded
     * in place. Downloads the build configs, archive indexes and ARM64 client, then adds the [TARGET_PRODUCT] row to
     * [BUILD_INFO] last, so an interrupted install is simply resumed the next time. Game assets that aren't on disk yet
     * are streamed from the CDN by the game client while playing.
     */
    fun install(gameRoot: File, onStatus: (String) -> Unit) {
        onStatus("Checking the latest World of Warcraft build...")
        val target = fetchRemote() ?: throw IOException("Couldn't reach Blizzard's patch server. Check your connection.")
        Files.createDirectories(gameRoot.canonicalFile.toPath())
        WowDownloadPolicy.destination(gameRoot, "Data/data/.layout", createParents = true)
        updateGame(gameRoot, target, onStatus)
    }

    fun updateGame(gameRoot: File, target: VersionCheckResult, onStatus: (String) -> Unit) {
        val cdn = Cdn(hosts = target.remoteCdnHosts, path = target.remoteCdnPath)
        onStatus("Fetching build ${target.remoteVersion} manifest...")
        val buildConfigRaw = cdn.config(target.remoteBuildConfig)
        val cdnConfigRaw = cdn.config(target.remoteCdnConfig)
        val cdnConfig = parseConfig(cdnConfigRaw.decodeToString())
        val buildConfig = parseConfig(buildConfigRaw.decodeToString())
        if (buildConfig["build-uid"]?.singleOrNull() != TARGET_PRODUCT) throw IOException("Wrong Blizzard build product")
        val archives = cdnConfig["archives"].orEmpty().map(WowDownloadPolicy::key)
        val indices = (archives + cdnConfig["file-index"].orEmpty().map(WowDownloadPolicy::key)).distinct()

        saveConfig(gameRoot, target.remoteBuildConfig, buildConfigRaw)
        saveConfig(gameRoot, target.remoteCdnConfig, cdnConfigRaw)
        syncIndices(gameRoot, cdn, indices, onStatus)
        syncClient(gameRoot, cdn, buildConfig, lazy { ArchiveLocator(cdn, gameRoot, archives) }, onStatus)

        onStatus("Updating build information...")
        upsertBuildInfo(WowDownloadPolicy.destination(gameRoot, BUILD_INFO), target)
        Files.deleteIfExists(WowDownloadPolicy.destination(gameRoot, UPDATE_MARKER).toPath())
    }

    fun download(gameRoot: File, onStatus: (String) -> Unit) {
        val latest = checkVersion(gameRoot)
            ?: throw IOException("Could not authenticate the build through Blizzard HTTPS. No client files were downloaded.")
        updateGame(gameRoot, latest, onStatus)
    }

    private fun syncClient(
        gameRoot: File,
        cdn: Cdn,
        buildConfig: Map<String, List<String>>,
        archives: Lazy<ArchiveLocator?>,
        onStatus: (String) -> Unit,
    ) {
        fun keys(name: String): List<String> = buildConfig[name]?.takeIf { it.size == 2 }?.map(WowDownloadPolicy::key)
            ?: throw IOException("Invalid CASC $name metadata")
        val (installCkey, installEkey) = keys("install")
        val encodedInstall = cdn.require("data", installEkey)
        verifyEncodedKey(encodedInstall, installEkey)
        val decodedInstall = blteDecode(encodedInstall)
        WowDownloadPolicy.verifyMd5(decodedInstall, installCkey)
        val files = parseInstall(decodedInstall).filter { it.isClientBinary() }
        if (files.none { it.relativePath.equals(EXE_NAME, ignoreCase = true) }) throw IOException("ARM64 executable missing from manifest")
        val paths = files.associateWith { entry ->
            WowDownloadPolicy.destination(gameRoot, "$FLAVOR_DIR/${entry.relativePath}")
        }
        if (paths.values.map { it.path.lowercase(Locale.ROOT) }.distinct().size != files.size) {
            throw IOException("Duplicate CASC output paths")
        }
        val pending = files.filter { md5Hex(paths.getValue(it)) != it.ckey }
        if (pending.isEmpty()) return

        onStatus("Looking up client files...")
        val ekeys = EncodingLookup(cdn, keys("encoding")[1]).find(pending.map { it.ckey }.toSet())
        val staged = mutableListOf<Pair<File, File>>()
        try {
            // Stage all binaries before replacing any of them. Publish the executable last.
            pending.sortedBy { it.relativePath.equals(EXE_NAME, ignoreCase = true) }.forEachIndexed { index, entry ->
                onStatus("Downloading ${entry.name} (${index + 1}/${pending.size})...")
                val ekey = ekeys[entry.ckey] ?: throw IOException("${entry.name} is missing from the encoding table")
                val encoded = cdn.fetch("data", ekey) ?: archives.value?.read(ekey)
                    ?: throw IOException("${entry.name} not found on the CDN")
                verifyEncodedKey(encoded, ekey)
                val dest = WowDownloadPolicy.destination(gameRoot, "$FLAVOR_DIR/${entry.relativePath}", createParents = true)
                val temp = WowDownloadPolicy.stage(dest) { out ->
                    val digest = MessageDigest.getInstance("MD5")
                    val size = blteDecode(encoded, DigestOutputStream(out, digest), entry.size)
                    if (size != entry.size || digest.digest().hex() != entry.ckey) {
                        throw IOException("Checksum or length mismatch for ${entry.name}")
                    }
                }
                staged += temp to dest
            }
            // If the process dies during publication, the next launch must repair the update
            // before running a mixture of binaries from two builds.
            writeAtomically(WowDownloadPolicy.destination(gameRoot, UPDATE_MARKER), byteArrayOf(1))
            staged.forEach { (temp, dest) -> WowDownloadPolicy.replace(temp, dest) }
        } finally {
            staged.forEach { (temp, _) -> Files.deleteIfExists(temp.toPath()) }
        }
    }

    private fun saveConfig(gameRoot: File, key: String, bytes: ByteArray) =
        writeAtomically(WowDownloadPolicy.destination(
            gameRoot, "Data/config/${key.take(2)}/${key.substring(2, 4)}/$key", createParents = true,
        ), bytes)

    // Patch archives and archive groups aren't needed to play; missing archives stream in the guest.
    private fun syncIndices(gameRoot: File, cdn: Cdn, keys: List<String>, onStatus: (String) -> Unit) {
        val done = AtomicInteger()
        val pool = Executors.newFixedThreadPool(INDEX_DOWNLOAD_THREADS)
        try {
            keys.map { key ->
                pool.submit {
                    readIndex(cdn, gameRoot, key)
                    onStatus("Checking archive indexes (${done.incrementAndGet()}/${keys.size})...")
                }
            }.forEach { it.get() }
        } catch (e: ExecutionException) {
            throw e.cause ?: e
        } finally {
            pool.shutdownNow()
        }
    }

    private fun readIndex(cdn: Cdn, gameRoot: File, key: String): ByteArray? {
        WowDownloadPolicy.key(key)
        val file = WowDownloadPolicy.destination(gameRoot, "Data/indices/$key.index", createParents = true)
        if (file.isFile && file.length() in 28L..MAX_CHUNK.toLong()) {
            val local = file.readBytes()
            try {
                verifyIndex(local, key)
                return local
            } catch (_: IOException) {
                // Do not trust a previously downloaded (possibly HTTP) or modified index.
            }
        }
        val fetched = cdn.fetch("data", "$key.index") ?: run {
            if (file.exists()) throw IOException("Cannot replace invalid Blizzard archive index")
            return null
        }
        verifyIndex(fetched, key)
        writeAtomically(file, fetched)
        return fetched
    }

    internal fun verifyIndex(bytes: ByteArray, key: String) {
        val footer = bytes.size - 28
        if (footer <= 0 || footer % 4120 != 0) throw IOException("Unsupported CASC index layout")
        val pages = footer / 4120
        val toc = pages * 4096
        val layout = bytes.copyOfRange(footer + 8, footer + 16)
        val common = layout.contentEquals(byteArrayOf(1, 0, 0, 4, 4, 4, 16, 8))
        val loose = layout.contentEquals(byteArrayOf(1, 0, 0, 4, 0, 4, 16, 8))
        if (!common && !loose) throw IOException("Unsupported CASC index layout")
        WowDownloadPolicy.verifyMd5(bytes.copyOfRange(footer, bytes.size), key)
        fun verifyShortHash(start: Int, size: Int, expected: Int) {
            val md = MessageDigest.getInstance("MD5")
            md.update(bytes, start, size)
            if (!md.digest().copyOf(8).contentEquals(bytes.copyOfRange(expected, expected + 8))) {
                throw IOException("CASC index checksum mismatch")
            }
        }
        verifyShortHash(toc, footer - toc, footer)
        for (page in 0 until pages) verifyShortHash(page * 4096, 4096, toc + 16 * pages + page * 8)
    }

    private fun writeAtomically(dest: File, bytes: ByteArray) = WowDownloadPolicy.writeAtomically(dest) { it.write(bytes) }

    /**
     * Points the [TARGET_PRODUCT] row of [buildInfo] at [target], adding the row (and the file) when it's missing.
     * A new row copies the region of an existing beta row so an upgraded install keeps its region. The game streams
     * missing assets from the "CDN Servers" URLs and crashes on startup when there are none.
     */
    internal fun upsertBuildInfo(buildInfo: File, target: VersionCheckResult) {
        val lines = buildInfo.takeIf { it.isFile }?.readLines()?.filter { it.isNotBlank() }?.ifEmpty { null } ?: listOf(BUILD_INFO_HEADER)
        val header = psvHeader(lines.first())
        val updates = mapOf(
            "Active" to "1",
            "Version" to target.remoteVersion,
            "Build Key" to target.remoteBuildConfig,
            "CDN Key" to target.remoteCdnConfig,
            "CDN Hosts" to target.remoteCdnHosts.joinToString(" "),
            "CDN Path" to target.remoteCdnPath,
            "CDN Servers" to target.remoteCdnServers.joinToString(" "),
            "Product" to TARGET_PRODUCT,
        ).filterValues { it.isNotBlank() }

        val rows = lines.drop(1).map { line -> header.zip(line.split("|")).toMap() }
        val existing = rows.indexOfFirst { it["Product"] == TARGET_PRODUCT }
        val updated = if (existing >= 0) {
            rows.mapIndexed { idx, row -> if (idx == existing) row + updates else row }
        } else {
            val branch = rows.firstOrNull { it["Product"] == LEGACY_PRODUCT }?.get("Branch")?.takeIf { it.isNotBlank() } ?: defaultBranch()
            val region = branch.uppercase(Locale.ROOT)
            val tags = listOf("speech", "text").joinToString(":") { "Windows $PLATFORM_ARCH $region? enUS $it?" }
            rows + (mapOf("Branch" to branch, "Tags" to tags) + updates)
        }
        val text = (listOf(lines.first()) + updated.map { row -> header.joinToString("|") { row[it].orEmpty() } }).joinToString("\n") + "\n"
        writeAtomically(buildInfo, text.toByteArray())
    }

    // Best guess at the region the Battle.net app would record, for installs that have no beta row to copy it from.
    private fun defaultBranch() = when (Locale.getDefault().country.uppercase(Locale.ROOT)) {
        "KR" -> "kr"
        "TW", "HK" -> "tw"
        in EU_COUNTRIES -> "eu"
        else -> "us"
    }

    private data class InstallEntry(val name: String, val ckey: String, val size: Int, val tags: Set<String>) {
        val relativePath get() = name.replace('\\', '/')

        fun isClientBinary(): Boolean {
            if ("Windows" !in tags || PLATFORM_ARCH !in tags || EXCLUDED_ARCH in tags) return false
            val lower = name.lowercase()
            return !lower.startsWith("utils\\") || "exceptionhandler" in lower
        }
    }

    private class Cdn(val hosts: List<String>, val path: String) {
        fun require(kind: String, key: String) = fetch(kind, key) ?: throw IOException("Missing Blizzard CDN object")

        fun config(key: String): ByteArray = require("config", key).also { WowDownloadPolicy.verifyMd5(it, key) }

        fun fetch(kind: String, key: String, range: LongRange? = null): ByteArray? {
            val maxBytes = when {
                range != null -> {
                    val length = range.last - range.first + 1
                    if (range.first < 0 || length !in 1L..MAX_OBJECT.toLong()) throw IOException("Invalid CASC range")
                    length.toInt()
                }
                kind == "config" -> 1024 * 1024
                key.endsWith(".index") -> MAX_CHUNK
                else -> MAX_OBJECT
            }
            var failure: IOException? = null
            if (hosts.isEmpty()) throw IOException("No trusted Blizzard CDN")
            for (host in hosts) {
                val request = Request.Builder()
                    .url(WowDownloadPolicy.cdnUrl(host, path, kind, key))
                    .header("Accept-Encoding", "identity")
                    .apply { if (range != null) header("Range", "bytes=${range.first}-${range.last}") }
                    .build()
                try {
                    Net.strictHttp.newCall(request).execute().use { response ->
                        if (response.code == 403 || response.code == 404) return@use
                        if (range == null && response.code != 200 || range != null && response.code != 206) {
                            throw IOException("Unexpected Blizzard CDN response: ${response.code}")
                        }
                        if (range != null) {
                            val contentRange = response.header("Content-Range").orEmpty()
                            val prefix = "bytes ${range.first}-${range.last}/"
                            if (!contentRange.startsWith(prefix) ||
                                (contentRange.removePrefix(prefix).toLongOrNull() ?: 0) <= range.last
                            ) throw IOException("Unexpected Blizzard CDN range")
                        }
                        val body = response.body ?: throw IOException("Empty CDN response")
                        val bytes = readBounded(body, maxBytes)
                        if (range != null && bytes.size != maxBytes) throw IOException("Truncated Blizzard CDN range")
                        return bytes
                    }
                } catch (e: IOException) {
                    failure = e
                }
            }
            failure?.let { throw it }
            return null
        }
    }

    private fun readBounded(body: okhttp3.ResponseBody, max: Int): ByteArray {
        if (body.contentLength() > max || body.source().request(max.toLong() + 1)) {
            throw IOException("Blizzard response exceeds its size limit")
        }
        return body.bytes()
    }

    private class EncodingLookup(private val cdn: Cdn, private val ekey: String) {
        private val chunks: List<BlteChunk> = run {
            val size = headerSize(fetchEncoded(0, 8))
            if (size !in 36..1024 * 1024) throw IOException("Invalid CASC encoding header")
            val header = fetchEncoded(0, size)
            WowDownloadPolicy.verifyMd5(header, ekey)
            chunkTable(header, size)
        }

        private var cachedChunk: BlteChunk? = null
        private var cachedData: ByteArray = ByteArray(0)

        private fun read(start: Long, length: Int, expectedPageHash: String? = null): ByteArray {
            if (length !in 1..MAX_CHUNK || start < 0) throw IOException("Invalid CASC encoding read")
            val out = ByteArray(length)
            var written = 0
            for (chunk in chunks) {
                val chunkEnd = chunk.decodedOffset + chunk.decodedSize
                if (chunkEnd <= start || chunk.decodedOffset >= start + length) continue
                val from = maxOf(start, chunk.decodedOffset) - chunk.decodedOffset
                val count = (minOf(start + length, chunkEnd) - chunk.decodedOffset - from).toInt()
                val bytes = if (expectedPageHash != null && chunk.encodedSize == chunk.decodedSize + 1 &&
                    fetchEncoded(chunk.encodedOffset, 1)[0] == 'N'.code.toByte()
                ) {
                    // Large uncompressed chunks can be read by page because the page hash comes
                    // from the encoding index, whose own chunks were verified against the EKey.
                    fetchEncoded(chunk.encodedOffset + 1 + from, count)
                } else {
                    decodedChunk(chunk).copyOfRange(from.toInt(), from.toInt() + count)
                }
                bytes.copyInto(out, written)
                written += count
            }
            if (written != length) throw IOException("Truncated CASC encoding data")
            expectedPageHash?.let { WowDownloadPolicy.verifyMd5(out, it) }
            return out
        }

        private fun decodedChunk(chunk: BlteChunk): ByteArray {
            if (cachedChunk != chunk) {
                if (chunk.encodedSize > MAX_CHUNK || chunk.decodedSize > MAX_CHUNK) {
                    throw IOException("Oversized CASC encoding metadata chunk")
                }
                val encoded = fetchEncoded(chunk.encodedOffset, chunk.encodedSize)
                WowDownloadPolicy.verifyMd5(encoded, chunk.md5)
                cachedData = decodeChunk(encoded, chunk.decodedSize, 0)
                if (cachedData.size != chunk.decodedSize) throw IOException("CASC encoding chunk length mismatch")
                cachedChunk = chunk
            }
            return cachedData
        }

        private fun fetchEncoded(offset: Long, size: Int) =
            cdn.fetch("data", ekey, offset until offset + size) ?: throw IOException("Encoding file missing")

        fun find(wanted: Set<String>): Map<String, String> {
            val header = ByteBuffer.wrap(read(0, 22))
            if (header.get(0) != 'E'.code.toByte() || header.get(1) != 'N'.code.toByte() || header.get(2) != 1.toByte()) {
                throw IOException("Invalid CASC encoding file")
            }
            val ckeySize = header.get(3).toInt()
            val ekeySize = header.get(4).toInt()
            val pageSize = (header.getShort(5).toInt() and 0xFFFF) * 1024
            val pageCount = header.getInt(9)
            val especSize = header.getInt(18)
            if (ckeySize != 16 || ekeySize != 16 || pageSize !in 1024..65536 ||
                pageCount !in 1..262144 || especSize !in 0..MAX_CHUNK
            ) throw IOException("Invalid CASC encoding dimensions")
            val indexStart = 22L + especSize
            val indexEntrySize = ckeySize + 16
            val index = read(indexStart, pageCount * indexEntrySize)
            val firstKeys = (0 until pageCount).map { hex(index, it * indexEntrySize, ckeySize) }
            val pagesStart = indexStart + pageCount.toLong() * indexEntrySize

            val found = mutableMapOf<String, String>()
            wanted.groupBy { ckey -> firstKeys.binarySearch(ckey).let { if (it >= 0) it else -it - 2 } }
                .filterKeys { it >= 0 }
                .forEach { (page, ckeys) ->
                    val data = read(
                        pagesStart + page.toLong() * pageSize, pageSize, hex(index, page * indexEntrySize + ckeySize, 16),
                    )
                    var pos = 0
                    while (pos < data.size && data[pos].toInt() != 0) {
                        val keyCount = data[pos].toInt() and 0xFF
                        if (pos + 6 + ckeySize + keyCount * ekeySize > data.size) throw IOException("Truncated CASC encoding page")
                        val ckey = hex(data, pos + 6, ckeySize)
                        if (ckey in ckeys) found[ckey] = hex(data, pos + 6 + ckeySize, ekeySize)
                        pos += 6 + ckeySize + keyCount * ekeySize
                    }
                }
            return found
        }
    }

    private class ArchiveLocator(private val cdn: Cdn, private val gameRoot: File, private val archives: List<String>) {
        fun read(ekey: String): ByteArray? {
            for (archive in archives) {
                val index = readIndex(cdn, gameRoot, archive) ?: continue
                val buffer = ByteBuffer.wrap(index)
                for (block in 0 until index.size / 4096 * 4096 step 4096) {
                    var pos = block
                    while (pos <= block + 4096 - 24) {
                        if (hex(index, pos, 16) == ekey) {
                            val size = buffer.getInt(pos + 16).toLong() and 0xFFFFFFFFL
                            val offset = buffer.getInt(pos + 20).toLong() and 0xFFFFFFFFL
                            return cdn.fetch("data", archive, offset until offset + size)
                        }
                        pos += 24
                    }
                }
            }
            return null
        }
    }

    private fun fetchText(url: String): String =
        patchHttp.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (response.code != 200) throw IOException("Blizzard metadata HTTP ${response.code}")
            readBounded(response.body ?: throw IOException("Empty Blizzard metadata"), 64 * 1024).decodeToString()
        }

    private fun activeBuild(buildInfo: File): Map<String, String> {
        check(buildInfo.isFile) { "Missing $BUILD_INFO in ${buildInfo.parent}" }
        val rows = parsePsv(buildInfo.readText())
        return rows.firstOrNull { it["Product"] == TARGET_PRODUCT && it["Active"] == "1" }
            ?: rows.firstOrNull { it["Product"] == TARGET_PRODUCT }
            ?: rows.firstOrNull { it["Active"] == "1" }
            ?: rows.firstOrNull()
            ?: throw IllegalStateException("No build in $BUILD_INFO")
    }

    private fun parsePsv(text: String, strict: Boolean = false): List<Map<String, String>> {
        val lines = text.lines().filter { it.isNotBlank() && !it.startsWith("#") }
        val header = psvHeader(lines.firstOrNull() ?: return emptyList())
        if (header.distinct().size != header.size) return emptyList()
        return lines.drop(1).mapNotNull {
            val values = it.split("|")
            if (strict && values.size != header.size) null else header.zip(values).toMap()
        }
    }

    private fun psvHeader(line: String) = line.split("|").map { it.substringBefore("!") }

    private fun splitHosts(hosts: String) = hosts.split(" ").filter { it.isNotBlank() }

    private fun parseConfig(text: String): Map<String, List<String>> =
        text.lines().filter { "=" in it && !it.startsWith("#") }.associate { line ->
            line.substringBefore("=").trim() to line.substringAfter("=").trim().split(" ").filter { it.isNotEmpty() }
        }

    private fun parseInstall(data: ByteArray): List<InstallEntry> {
        if (data.size < 10 || data[0] != 'I'.code.toByte() || data[1] != 'N'.code.toByte()) {
            throw IOException("Bad CASC install manifest")
        }
        val buffer = ByteBuffer.wrap(data)
        val hashSize = data[3].toInt()
        val tagCount = buffer.getShort(4).toInt() and 0xFFFF
        val entryCount = buffer.getInt(6)
        if (hashSize != 16 || entryCount !in 1..100_000 || tagCount > 1024) throw IOException("Invalid CASC install table")
        val maskSize = (entryCount + 7) / 8
        var pos = 10

        fun readString(): String {
            val end = (pos until minOf(pos + 4096, data.size)).firstOrNull { data[it].toInt() == 0 }
                ?: throw IOException("Invalid CASC install entry")
            return String(data, pos, end - pos).also { pos = end + 1 }
        }

        val tags = (0 until tagCount).map {
            val name = readString()
            pos += 2
            if (pos + maskSize > data.size) throw IOException("Truncated CASC install mask")
            (name to data.copyOfRange(pos, pos + maskSize)).also { pos += maskSize }
        }
        return (0 until entryCount).map { i ->
            val name = readString()
            if (pos + hashSize + 4 > data.size) throw IOException("Truncated CASC install table")
            val ckey = hex(data, pos, hashSize)
            val size = buffer.getInt(pos + hashSize)
            pos += hashSize + 4
            val entryTags = tags.filter { (_, mask) -> mask[i / 8].toInt() and (0x80 ushr (i % 8)) != 0 }.map { it.first }.toSet()
            InstallEntry(name, ckey, size, entryTags)
        }
    }

    private data class BlteChunk(
        val encodedOffset: Long,
        val encodedSize: Int,
        val decodedOffset: Long,
        val decodedSize: Int,
        val md5: String,
    )

    private fun headerSize(data: ByteArray): Int {
        if (data.size < 8 || String(data, 0, 4, Charsets.US_ASCII) != "BLTE") throw IOException("Not BLTE data")
        return ByteBuffer.wrap(data).getInt(4)
    }

    private fun chunkTable(data: ByteArray, size: Int): List<BlteChunk> {
        if (size !in 36..minOf(data.size, 1024 * 1024) || data[8] != 0x0F.toByte()) {
            throw IOException("Invalid BLTE header")
        }
        val buffer = ByteBuffer.wrap(data)
        val count = buffer.getInt(8) and 0xFFFFFF
        if (size != 12 + count * 24) throw IOException("Invalid BLTE chunk table")
        var encoded = size.toLong()
        var decoded = 0L
        return (0 until count).map { i ->
            val offset = 12 + i * 24
            val compressed = buffer.getInt(offset)
            val uncompressed = buffer.getInt(offset + 4)
            if (compressed !in 1..MAX_OBJECT || uncompressed !in 0..MAX_OBJECT) throw IOException("Invalid BLTE chunk size")
            BlteChunk(encoded, compressed, decoded, uncompressed, hex(data, offset + 8, 16)).also {
                encoded += compressed
                decoded += uncompressed
            }
        }
    }

    internal fun verifyEncodedKey(data: ByteArray, key: String) {
        val size = headerSize(data)
        if (size == 0) {
            WowDownloadPolicy.verifyMd5(data, key)
        } else {
            chunkTable(data, size)
            WowDownloadPolicy.verifyMd5(data.copyOf(size), key)
        }
    }

    internal fun blteDecode(data: ByteArray, limit: Int = MAX_CHUNK): ByteArray =
        ByteArrayOutputStream().also { blteDecode(data, it, limit) }.toByteArray()

    private fun blteDecode(data: ByteArray, out: OutputStream, limit: Int, depth: Int = 0): Int {
        if (limit !in 0..MAX_OBJECT || depth > 4) throw IOException("BLTE decoding limit exceeded")
        val size = headerSize(data)
        if (size == 0) {
            val decoded = decodeChunk(data.copyOfRange(8, data.size), limit, depth)
            out.write(decoded)
            return decoded.size
        }
        var written = 0
        val chunks = chunkTable(data, size)
        val last = chunks.last()
        if (last.encodedOffset + last.encodedSize != data.size.toLong() ||
            last.decodedOffset + last.decodedSize > limit
        ) throw IOException("Invalid BLTE object dimensions")
        for (chunk in chunks) {
            val offset = chunk.encodedOffset.toInt()
            val encoded = data.copyOfRange(offset, offset + chunk.encodedSize)
            WowDownloadPolicy.verifyMd5(encoded, chunk.md5)
            val decoded = decodeChunk(encoded, chunk.decodedSize, depth)
            if (decoded.size != chunk.decodedSize) throw IOException("BLTE chunk length mismatch")
            out.write(decoded)
            written += decoded.size
        }
        return written
    }

    private fun decodeChunk(chunk: ByteArray, limit: Int, depth: Int): ByteArray {
        if (chunk.isEmpty()) throw IOException("Empty BLTE chunk")
        val body = chunk.copyOfRange(1, chunk.size)
        return when (chunk[0].toInt().toChar()) {
            'N' -> body.also { if (it.size > limit) throw IOException("Oversized BLTE chunk") }
            'Z' -> inflate(body, limit)
            'F' -> ByteArrayOutputStream().also { blteDecode(body, it, limit, depth + 1) }.toByteArray()
            else -> throw IOException("Unsupported BLTE chunk mode")
        }
    }

    private fun inflate(body: ByteArray, limit: Int): ByteArray {
        val inflater = Inflater()
        inflater.setInput(body)
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(64 * 1024)
        try {
            while (!inflater.finished()) {
                val n = inflater.inflate(buffer)
                if (n == 0 && !inflater.finished()) throw IOException("Truncated zlib chunk")
                if (out.size() + n > limit) throw IOException("Oversized zlib chunk")
                out.write(buffer, 0, n)
            }
            if (inflater.remaining != 0) throw IOException("Unexpected trailing zlib data")
        } catch (e: java.util.zip.DataFormatException) {
            throw IOException("Invalid zlib chunk", e)
        } finally {
            inflater.end()
        }
        return out.toByteArray()
    }

    private fun md5Hex(file: File): String? {
        if (!file.isFile) return null
        val digest = MessageDigest.getInstance("MD5")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
            }
        }
        return hex(digest.digest(), 0, 16)
    }

    private fun hex(data: ByteArray, offset: Int, size: Int): String {
        val chars = CharArray(size * 2)
        for (i in 0 until size) {
            val v = data[offset + i].toInt() and 0xFF
            chars[i * 2] = HEX[v ushr 4]
            chars[i * 2 + 1] = HEX[v and 0xF]
        }
        return String(chars)
    }

    private val HEX = "0123456789abcdef".toCharArray()
}

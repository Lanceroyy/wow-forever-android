package app.gamenative.ui.screen.wow

import android.content.Context
import app.gamenative.BuildConfig
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.security.MessageDigest
import java.util.Comparator
import java.util.zip.ZipInputStream
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.zstandard.ZstdCompressorInputStream

internal object BundledComponents {
    private const val MARKER = ".wow-verified-sha256"
    private const val MAX_EXTRACTED_BYTES = 2L * 1024 * 1024 * 1024
    private val pins = BuildConfig.BUNDLED_COMPONENT_PINS.split(",").associate { it.substringBefore(':') to it.substringAfter(':') }

    @Synchronized
    fun install(context: Context, asset: String, destination: File, required: List<String>, onStatus: () -> Unit) {
        val expected = pins[asset] ?: throw IOException("Unpinned bundled component")
        val target = destination.toPath()
        val marker = target.resolve(MARKER)
        if (Files.isDirectory(target, NOFOLLOW_LINKS) && Files.isRegularFile(marker, NOFOLLOW_LINKS) &&
            Files.size(marker) == expected.length.toLong() && marker.toFile().readText() == expected &&
            required.all { target.resolve(it).toFile().isFile }
        ) return

        onStatus()
        val assetPath = "bundled_components/$asset"
        val digest = MessageDigest.getInstance("SHA-256")
        context.assets.open(assetPath).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        if (digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) } != expected) {
            throw IOException("Bundled component checksum failed: $asset")
        }

        // APK assets are immutable. Extract the same verified asset into a fresh private directory.
        val parent = Files.createDirectories(target.parent!!)
        val stage = Files.createTempDirectory(parent, ".wow-component-")
        try {
            context.assets.open(assetPath).use { input ->
                if (asset.endsWith(".zip")) extractZip(input, stage) else extractWcp(input, stage)
            }
            if (!required.all { stage.resolve(it).toFile().isFile }) throw IOException("Incomplete bundled component: $asset")
            stage.resolve(MARKER).toFile().writeText(expected)
            val backup = Files.createTempDirectory(parent, ".wow-old-component-")
            val previous = backup.resolve("component")
            try {
                if (Files.exists(target, NOFOLLOW_LINKS)) Files.move(target, previous, ATOMIC_MOVE)
                try {
                    Files.move(stage, target, ATOMIC_MOVE)
                } catch (e: IOException) {
                    if (Files.exists(previous, NOFOLLOW_LINKS)) Files.move(previous, target, ATOMIC_MOVE)
                    throw e
                }
                deleteTree(backup)
            } finally {
                // Preserve the backup if restoring it failed.
                if (Files.notExists(previous, NOFOLLOW_LINKS)) Files.deleteIfExists(backup)
            }
        } finally {
            deleteTree(stage)
        }
    }

    internal fun archivePath(root: Path, name: String): Path {
        val relative = name.removePrefix("./").trimEnd('/')
        if (relative.isEmpty() || relative.split('/').any { it.isEmpty() || it == "." || it == ".." } ||
            relative.any { it == '\\' || it == ':' || it.code < 32 } || relative == MARKER
        ) throw IOException("Unsafe component archive path")
        val path = root.resolve(relative)
        if (!path.startsWith(root)) throw IOException("Component archive escapes its destination")
        return path
    }

    internal fun linkTarget(root: Path, path: Path, value: String): Path {
        if (value.isEmpty() || value.any { it == '\\' || it == ':' || it.code < 32 }) {
            throw IOException("Unsafe component link")
        }
        val link = Paths.get(value)
        if (link.isAbsolute || !path.parent.resolve(link).normalize().startsWith(root)) {
            throw IOException("Component link escapes its destination")
        }
        return link
    }

    private fun extractZip(input: InputStream, root: Path) = ZipInputStream(input).use { zip ->
        var remaining = MAX_EXTRACTED_BYTES
        while (true) {
            val entry = zip.nextEntry ?: break
            val path = archivePath(root, entry.name)
            if (entry.isDirectory) {
                Files.createDirectories(path)
            } else {
                remaining -= writeEntry(zip, path, remaining)
            }
            zip.closeEntry()
        }
    }

    private fun extractWcp(input: InputStream, root: Path) =
        TarArchiveInputStream(ZstdCompressorInputStream(input)).use { tar ->
            val links = mutableListOf<Pair<Path, Path>>()
            var remaining = MAX_EXTRACTED_BYTES
            while (true) {
                val entry = tar.nextEntry ?: break
                if (!entry.isCheckSumOK || !tar.canReadEntryData(entry)) throw IOException("Invalid component archive entry")
                if (entry.isDirectory && entry.name in setOf("./", ".")) continue
                val path = archivePath(root, entry.name)
                when {
                    entry.isDirectory -> Files.createDirectories(path)
                    entry.isSymbolicLink -> links += path to linkTarget(root, path, entry.linkName)
                    entry.isFile && !entry.isSparse && !entry.isLink -> {
                        val length = writeEntry(tar, path, remaining)
                        if (length != entry.size) throw IOException("Truncated component archive entry")
                        remaining -= length
                    }
                    else -> throw IOException("Unsupported component archive entry")
                }
            }
            // No extraction write can follow an archive-provided symlink.
            for ((path, link) in links) {
                Files.createDirectories(path.parent)
                Files.createSymbolicLink(path, link)
            }
        }

    private fun writeEntry(input: InputStream, path: Path, limit: Long): Long {
        Files.createDirectories(path.parent)
        val buffer = ByteArray(64 * 1024)
        var written = 0L
        Files.newOutputStream(path, java.nio.file.StandardOpenOption.CREATE_NEW, java.nio.file.StandardOpenOption.WRITE).use { out ->
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                written += count
                if (written > limit) throw IOException("Component archive exceeds size limit")
                out.write(buffer, 0, count)
            }
        }
        if (!path.toFile().setExecutable(true, true)) throw IOException("Could not set component file mode")
        return written
    }

    private fun deleteTree(root: Path) {
        if (Files.notExists(root, NOFOLLOW_LINKS)) return
        Files.walk(root).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { Files.delete(it) } }
    }
}

package app.gamenative.ui.screen.wow

import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardCopyOption.REPLACE_EXISTING
import java.security.MessageDigest
import java.util.Locale

internal object WowDownloadPolicy {
    const val CDN_PATH = "tpr/wow"
    private val cdnHosts = setOf("level3.ssl.blizzard.com", "us.cdn.blizzard.com")
    private val keyFormat = Regex("[0-9a-f]{32}")
    private val serverFormat = Regex(
        """https://(level3[.]ssl[.]blizzard[.]com|us[.]cdn[.]blizzard[.]com)/(?:[?](?:maxhosts|fallback)=[0-9]+(?:&(?:maxhosts|fallback)=[0-9]+)*)?""",
    )

    fun cdnHost(server: String): String? = serverFormat.matchEntire(server)?.groupValues?.get(1)

    fun key(value: String): String = value.also {
        if (!keyFormat.matches(it)) throw IOException("Invalid CASC content key")
    }

    fun cdnUrl(host: String, path: String, kind: String, objectName: String): String {
        if (host !in cdnHosts || path != CDN_PATH || kind !in setOf("config", "data")) {
            throw IOException("Untrusted Blizzard CDN location")
        }
        val key = key(if (kind == "data") objectName.removeSuffix(".index") else objectName)
        return "https://$host/$path/$kind/${key.take(2)}/${key.substring(2, 4)}/$objectName"
    }

    fun verifyMd5(bytes: ByteArray, expected: String) {
        key(expected)
        if (MessageDigest.getInstance("MD5").digest(bytes).hex() != expected) {
            throw IOException("CASC checksum mismatch")
        }
    }

    fun ByteArray.hex(): String = joinToString("") { "%02x".format(Locale.ROOT, it.toInt() and 255) }

    /** CASC names use Windows separators; accept only relative regular-file paths. */
    fun destination(root: File, relative: String, createParents: Boolean = false): File {
        val parts = relative.replace('\\', '/').split('/')
        if (parts.any {
                it.isEmpty() || it == "." || it == ".." || it.last() == '.' || it.last() == ' ' ||
                    it.any { char -> char.code < 32 || char in ":<>\"|?*" }
            }
        ) throw IOException("Unsafe CASC file path")

        var path = root.canonicalFile.toPath()
        for (part in parts.dropLast(1)) {
            path = path.resolve(part)
            if (createParents && Files.notExists(path, NOFOLLOW_LINKS)) {
                try {
                    Files.createDirectory(path)
                } catch (_: java.nio.file.FileAlreadyExistsException) {
                    // Index downloads may create the same directory concurrently.
                }
            }
            if (!Files.notExists(path, NOFOLLOW_LINKS) && !Files.isDirectory(path, NOFOLLOW_LINKS)) {
                throw IOException("Unsafe CASC parent directory")
            }
        }
        val dest = path.resolve(parts.last())
        if (!Files.notExists(dest, NOFOLLOW_LINKS) && !Files.isRegularFile(dest, NOFOLLOW_LINKS)) {
            throw IOException("Unsafe CASC output file")
        }
        return dest.toFile()
    }

    fun stage(dest: File, write: (OutputStream) -> Unit): File {
        val temporary = Files.createTempFile(dest.parentFile!!.toPath(), ".wow-", ".part").toFile()
        var verified = false
        try {
            temporary.outputStream().buffered().use(write)
            verified = true
            return temporary
        } finally {
            if (!verified) Files.deleteIfExists(temporary.toPath())
        }
    }

    fun replace(temporary: File, dest: File) {
        Files.move(temporary.toPath(), dest.toPath(), ATOMIC_MOVE, REPLACE_EXISTING)
    }

    fun writeAtomically(dest: File, write: (OutputStream) -> Unit) {
        val temporary = stage(dest, write)
        try {
            replace(temporary, dest)
        } finally {
            Files.deleteIfExists(temporary.toPath())
        }
    }
}

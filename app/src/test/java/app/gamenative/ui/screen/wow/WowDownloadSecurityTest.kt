package app.gamenative.ui.screen.wow

import app.gamenative.ui.screen.wow.WowDownloadPolicy.hex
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.security.MessageDigest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class WowDownloadSecurityTest {
    @get:Rule val folder = TemporaryFolder()
    private val key = "a".repeat(32)
    private val otherKey = "b".repeat(32)
    private val versions = """
        Region!STRING:0|BuildConfig!HEX:16|CDNConfig!HEX:16|VersionsName!STRING:0
        ## seqn = 10
        us|$key|$otherKey|1.60.1.70862
    """.trimIndent()
    private val cdns = """
        Name!STRING:0|Path!STRING:0|Hosts!STRING:0|Servers!STRING:0|ConfigPath!STRING:0
        us|tpr/wow|level3.blizzard.com|http://level3.blizzard.com/?maxhosts=8 https://level3.ssl.blizzard.com/?maxhosts=4&fallback=1|tpr/configs/data
    """.trimIndent()

    @Test fun parsesTactV2WithoutRetainingAdvertisedHttpRoutes() {
        val target = WowClientDownloader.parseRemote(versions, cdns)
        assertEquals(listOf("level3.ssl.blizzard.com"), target.remoteCdnHosts)
        assertEquals(listOf("https://level3.ssl.blizzard.com/?maxhosts=4&fallback=1"), target.remoteCdnServers)
        assertEquals(key, target.remoteBuildConfig)
        assertEquals("https://level3.ssl.blizzard.com/tpr/wow/config/aa/aa/$key",
            WowDownloadPolicy.cdnUrl(target.remoteCdnHosts.single(), target.remoteCdnPath, "config", key))
    }

    @Test fun upgradesAnExistingCdnRowWithoutChangingBuildOrOtherProducts() {
        val build = folder.newFile()
        val other = "1|wow|tpr/wow|level3.blizzard.com|http://level3.blizzard.com/|1.2.3"
        build.writeText(
            "Active!DEC:1|Product!STRING:0|CDN Path!STRING:0|CDN Hosts!STRING:0|CDN Servers!STRING:0|Version!STRING:0\n" +
                "$other\n1|wowf|tpr/wow|level3.blizzard.com|http://level3.blizzard.com/|1.60.1.70862\n",
        )
        val secure = "https://level3.ssl.blizzard.com/?maxhosts=4&fallback=1"
        WowClientDownloader.rewriteGuestCdnRoutes(build, listOf(secure))
        val row = WowClientDownloader.readBuildInfo(build)!!
        assertEquals(secure, row["CDN Servers"])
        assertEquals("1.60.1.70862", row["Version"])
        assertEquals(other, build.readLines()[1])
    }

    @Test fun rejectsTamperedVersionsAndCdnRoutes() {
        assertThrows(IOException::class.java) { WowClientDownloader.parseRemote(versions.replace(key, "../../a"), cdns) }
        assertThrows(IOException::class.java) { WowClientDownloader.parseRemote(versions, cdns.replace("tpr/wow", "../wow")) }
        assertThrows(IOException::class.java) { WowClientDownloader.parseRemote(versions, cdns.replace(".com/?maxhosts=4", ".com.evil/?maxhosts=4")) }
        assertThrows(IOException::class.java) { WowClientDownloader.parseRemote(versions, cdns.replace("https:", "http:")) }
        assertThrows(IOException::class.java) { WowDownloadPolicy.cdnUrl("level3.ssl.blizzard.com@evil", "tpr/wow", "data", key) }
    }

    @Test fun rejectsEscapesInManifestAndArchives() {
        val root = folder.newFolder()
        for (path in listOf("../payload", "bin/../payload", "/absolute", "C:\\payload", "data/a.dll:stream", "data//a")) {
            assertThrows(IOException::class.java) { WowDownloadPolicy.destination(root, path) }
        }
        for (path in listOf("../payload", "/absolute", "C:\\payload", "dir//a")) {
            assertThrows(IOException::class.java) { BundledComponents.archivePath(root.toPath(), path) }
        }
        assertThrows(IOException::class.java) {
            BundledComponents.linkTarget(root.toPath(), root.toPath().resolve("dir/wine"), "../../other")
        }
    }

    @Test fun truncatedWriteLeavesOldExecutableAndNoTempFile() {
        val root = folder.newFolder()
        val exe = File(root, "Wow-ARM64.exe").apply { writeText("existing") }
        assertThrows(IOException::class.java) {
            WowDownloadPolicy.writeAtomically(exe) { out ->
                out.write(byteArrayOf(1, 2))
                throw IOException("simulated interrupted transfer or corrupt decoder")
            }
        }
        assertEquals("existing", exe.readText())
        assertEquals(listOf("Wow-ARM64.exe"), root.list()!!.toList())
        WowDownloadPolicy.writeAtomically(exe) { it.write("replacement".toByteArray()) }
        assertEquals("replacement", exe.readText())
    }

    @Test fun verifiesEncodedHeaderAndRejectsCorruptChunksAndTrailingData() {
        val chunk = "Nhello".toByteArray()
        val header = ByteBuffer.allocate(36).put("BLTE".toByteArray()).putInt(36).putInt(0x0F000001)
            .putInt(chunk.size).putInt(5).put(md5(chunk)).array()
        val expected = md5(header).hex()
        val payload = header + chunk
        WowClientDownloader.verifyEncodedKey(payload, expected)
        assertArrayEquals("hello".toByteArray(), WowClientDownloader.blteDecode(payload))
        assertThrows(IOException::class.java) { WowClientDownloader.verifyEncodedKey(payload, key) }
        assertThrows(IOException::class.java) { WowClientDownloader.blteDecode(payload.copyOf(payload.size - 1)) }
        assertThrows(IOException::class.java) { WowClientDownloader.blteDecode(payload + byteArrayOf(0)) }
        payload[payload.lastIndex] = 'x'.code.toByte()
        assertThrows(IOException::class.java) { WowClientDownloader.blteDecode(payload) }
    }

    @Test fun indexValidatesItsFooterTocAndPages() {
        val page = ByteArray(4096)
        val toc = ByteArray(16) + md5(page).copyOf(8)
        val footer = md5(toc).copyOf(8) + byteArrayOf(1, 0, 0, 4, 4, 4, 16, 8) + ByteArray(12)
        val index = page + toc + footer
        val key = md5(footer).hex()
        WowClientDownloader.verifyIndex(index, key)
        for (offset in listOf(0, 4096, index.lastIndex)) {
            val tampered = index.copyOf().also { it[offset] = (it[offset].toInt() xor 1).toByte() }
            assertThrows(IOException::class.java) { WowClientDownloader.verifyIndex(tampered, key) }
        }
    }

    private fun md5(bytes: ByteArray) = MessageDigest.getInstance("MD5").digest(bytes)
}

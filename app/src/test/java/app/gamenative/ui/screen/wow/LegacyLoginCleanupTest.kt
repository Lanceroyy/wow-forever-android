package app.gamenative.ui.screen.wow

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class LegacyLoginCleanupTest {
    @get:Rule val folder = TemporaryFolder()

    @Test fun unlinksOnlyKnownHandoffsAndCanRunTwice() {
        val root = folder.newFolder()
        val current = File(root, "_forever_").apply { mkdir() }
        val beta = File(root, "_classic_beta_").apply { mkdir() }
        val handoffs = listOf(File(current, "login.txt"), File(beta, "login.txt"))
        handoffs.forEach { it.writeText("test-only sentinel") }
        val config = File(current, "Config.wtf").apply { writeText("untouched") }
        repeat(2) { BattleNetSignIn.removeLoginFile(root) }
        handoffs.forEach { assertFalse(it.exists()) }
        assertEquals("untouched", config.readText())
    }

    @Test fun unlinkDoesNotFollowLoginFileSymlink() {
        val root = folder.newFolder()
        val game = File(root, "_forever_").apply { mkdir() }
        val target = folder.newFile().apply { writeText("untouched") }
        val link = File(game, "login.txt").toPath()
        try {
            Files.createSymbolicLink(link, target.toPath())
        } catch (e: java.io.IOException) {
            org.junit.Assume.assumeNoException(e)
        }
        BattleNetSignIn.removeLoginFile(root)
        assertFalse(Files.isSymbolicLink(link))
        assertEquals("untouched", target.readText())
    }
}

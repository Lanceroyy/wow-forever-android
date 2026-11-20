package app.gamenative.ui.screen.wow

import android.content.Context
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.LinkOption

/** Migration only. This launcher does not collect or hand off Battle.net credentials. */
object BattleNetSignIn {
    fun removeSavedLogin(context: Context) {
        // Do not load or decrypt the obsolete preferences, even during migration.
        context.deleteSharedPreferences("battle_net_login")
    }

    @Throws(IOException::class)
    fun removeLoginFile(gameRoot: File) {
        val root = gameRoot.canonicalFile.toPath()
        for (flavor in listOf(WowClientDownloader.FLAVOR_DIR, WowClientDownloader.LEGACY_FLAVOR_DIR)) {
            val dir = root.resolve(flavor)
            if (Files.notExists(dir, LinkOption.NOFOLLOW_LINKS)) continue
            if (!Files.isDirectory(dir, LinkOption.NOFOLLOW_LINKS)) {
                throw IOException("Cannot safely clean up the old login handoff in $flavor")
            }
            val handoff = dir.resolve("login.txt")
            if (Files.isDirectory(handoff, LinkOption.NOFOLLOW_LINKS)) {
                throw IOException("Cannot safely remove a directory named login.txt")
            }
            // Unlink the entry. Overwriting it would follow a possible symlink/hard link.
            Files.deleteIfExists(handoff)
        }
    }
}

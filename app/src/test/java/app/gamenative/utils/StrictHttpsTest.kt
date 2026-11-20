package app.gamenative.utils

import app.gamenative.ui.screen.wow.WowDownloadPolicy
import java.io.File
import java.io.IOException
import javax.net.ssl.SSLHandshakeException
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class StrictHttpsTest {
    private val cert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
    private val serverCerts = HandshakeCertificates.Builder().heldCertificate(cert).build()

    @Test fun permitsKnownRuntimeArchivesAndRejectsEscapes() {
        assertTrue(Net.isRuntimeComponentPath("imagefs_bionic.txz"))
        assertTrue(Net.isRuntimeComponentPath("graphics_driver/libvulkan_wrapper.tar.xz"))
        assertTrue(Net.isRuntimeComponentPath("container_files/extras.tzst"))
        assertFalse(Net.isRuntimeComponentPath("container_files/../extras.tzst"))
        assertFalse(Net.isRuntimeComponentPath("another.txz"))
    }

    @Test fun refusesHttpBeforeConnecting() {
        MockWebServer().use { server ->
            val request = Request.Builder().url(server.url("/")).build()
            assertThrows(IOException::class.java) { Net.strictHttp.newCall(request).execute().close() }
            assertEquals(0, server.requestCount)
        }
    }

    @Test fun rejectsAnUntrustedTlsCertificate() {
        MockWebServer().use { server ->
            server.useHttps(serverCerts.sslSocketFactory(), false)
            server.enqueue(MockResponse().setBody("not trusted"))
            server.start(java.net.InetAddress.getByName("127.0.0.1"), 0)
            val request = Request.Builder().url(server.url("/").newBuilder().host("127.0.0.1").build()).build()
            val client = Net.httpsWithoutRedirects(OkHttpClient.Builder().retryOnConnectionFailure(false).build())
            assertThrows(SSLHandshakeException::class.java) { client.newCall(request).execute().close() }
        }
    }

    @Test fun interruptedTlsBodyDoesNotReplaceAnExistingFile() {
        val root = kotlin.io.path.createTempDirectory().toFile()
        try {
            val existing = File(root, "existing.exe").apply { writeText("original") }
            val clientCerts = HandshakeCertificates.Builder().addTrustedCertificate(cert.certificate).build()
            val client = Net.httpsWithoutRedirects(
                OkHttpClient.Builder().sslSocketFactory(clientCerts.sslSocketFactory(), clientCerts.trustManager)
                    .retryOnConnectionFailure(false).build(),
            )
            MockWebServer().use { server ->
                server.useHttps(serverCerts.sslSocketFactory(), false)
                server.enqueue(MockResponse().setBody("x".repeat(32768)).setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY))
                assertThrows(IOException::class.java) {
                    WowDownloadPolicy.writeAtomically(existing) { out ->
                        client.newCall(Request.Builder().url(server.url("/")).build()).execute().use { response ->
                            response.body!!.byteStream().copyTo(out)
                        }
                    }
                }
            }
            assertEquals("original", existing.readText())
            assertEquals(listOf("existing.exe"), root.list()!!.toList())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test fun trustedTlsRedirectIsNeverFollowedAutomatically() {
        val clientCerts = HandshakeCertificates.Builder().addTrustedCertificate(cert.certificate).build()
        val client = Net.httpsWithoutRedirects(
            OkHttpClient.Builder().sslSocketFactory(clientCerts.sslSocketFactory(), clientCerts.trustManager).build(),
        )
        MockWebServer().use { server ->
            server.useHttps(serverCerts.sslSocketFactory(), false)
            server.enqueue(MockResponse().setResponseCode(302).addHeader("Location", "http://localhost/downgrade"))
            client.newCall(Request.Builder().url(server.url("/")).build()).execute().use {
                assertEquals(302, it.code)
            }
            assertEquals(1, server.requestCount)
        }
    }
}

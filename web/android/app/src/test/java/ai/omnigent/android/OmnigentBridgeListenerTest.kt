package ai.omnigent.android

import android.app.Application
import android.app.NotificationManager
import android.content.Context
import androidx.appcompat.app.AppCompatDelegate
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.ShadowNotificationManager

/**
 * Bridge JSON parsing, asserted end to end through the real
 * [NativeNotificationManager] into Robolectric's shadow notification
 * service — the same wiring [MainActivity] installs.
 */
@RunWith(RobolectricTestRunner::class)
class OmnigentBridgeListenerTest {
    private lateinit var context: Application
    private lateinit var listener: OmnigentBridgeListener
    private lateinit var shadow: ShadowNotificationManager
    private lateinit var speech: RecordingSpeech

    private val badgeId = 1

    @Before
    fun setUp() {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
        context = ApplicationProvider.getApplicationContext()
        speech = RecordingSpeech()
        listener =
            OmnigentBridgeListener(
                notifications = NativeNotificationManager(context),
                blobSaver = BlobSaver(context),
                speech = speech,
            )
        shadow =
            shadowOf(
                context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager,
            )
    }

    @Test
    fun `setColorScheme light sets night mode no`() {
        listener.handle("""{"method":"setColorScheme","scheme":"light"}""")
        assertEquals(AppCompatDelegate.MODE_NIGHT_NO, AppCompatDelegate.getDefaultNightMode())
    }

    @Test
    fun `setColorScheme dark sets night mode yes`() {
        listener.handle("""{"method":"setColorScheme","scheme":"dark"}""")
        assertEquals(AppCompatDelegate.MODE_NIGHT_YES, AppCompatDelegate.getDefaultNightMode())
    }

    @Test
    fun `setColorScheme system follows system`() {
        listener.handle("""{"method":"setColorScheme","scheme":"system"}""")
        assertEquals(
            AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM,
            AppCompatDelegate.getDefaultNightMode(),
        )
    }

    @Test
    fun `setColorScheme rejects missing and unsupported schemes`() {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)

        listener.handle("""{"method":"setColorScheme"}""")
        listener.handle("""{"method":"setColorScheme","scheme":"auto"}""")
        listener.handle("""{"method":"setColorScheme","scheme":123}""")

        assertEquals(AppCompatDelegate.MODE_NIGHT_NO, AppCompatDelegate.getDefaultNightMode())
    }

    @Test
    fun `setBadgeCount message posts the badge with parsed fields`() {
        listener.handle(
            """{"method":"setBadgeCount","count":3,"navigatePath":"/inbox","title":"T","body":"B"}""",
        )

        val posted = shadow.getNotification(badgeId)
        assertNotNull(posted)
        assertEquals(3, posted!!.number)
        assertEquals(
            "/inbox",
            shadowOf(posted.contentIntent).savedIntent.getStringExtra(
                NativeNotificationManager.EXTRA_NAVIGATE_PATH,
            ),
        )
    }

    @Test
    fun `setBadgeCount zero clears the badge`() {
        listener.handle("""{"method":"setBadgeCount","count":2,"navigatePath":"/inbox"}""")
        listener.handle("""{"method":"setBadgeCount","count":0}""")
        assertNull(shadow.getNotification(badgeId))
    }

    @Test
    fun `legacy setBadgeCount without options still posts`() {
        // Older web builds send only the count; fields default to absent.
        listener.handle("""{"method":"setBadgeCount","count":1}""")
        val posted = shadow.getNotification(badgeId)
        assertNotNull(posted)
        assertNull(posted!!.contentIntent)
    }

    @Test
    fun `notify message posts a per-session toast with tap routing`() {
        listener.handle(
            """{"method":"notify","params":{"title":"done","body":"b","navigatePath":"/c/x"}}""",
        )

        // Toasts allocate ids above the reserved badge id.
        assertEquals(1, shadow.allNotifications.size)
        assertNull(shadow.getNotification(badgeId))
    }

    @Test
    fun `notify without a title is dropped`() {
        listener.handle("""{"method":"notify","params":{"body":"b"}}""")
        assertEquals(0, shadow.allNotifications.size)
    }

    @Test
    fun `speak and stop messages route to native speech`() {
        listener.handle(
            """{"method":"speak","params":{"text":"Status ready","language":"en-GB","rate":1.25}}""",
        )
        listener.handle("""{"method":"stopSpeaking"}""")

        assertEquals("Status ready", speech.text)
        assertEquals("en-GB", speech.language)
        assertEquals(1.25f, speech.rate)
        assertEquals(1, speech.stopCount)
    }

    @Test
    fun `speak without text is dropped`() {
        listener.handle("""{"method":"speak","params":{"language":"en-US"}}""")
        assertNull(speech.text)
    }

    @Test
    fun `malformed and unknown messages are dropped without crashing`() {
        listener.handle("not json at all")
        listener.handle("""{"method":"unknownThing","count":5}""")
        listener.handle("""{"count":5}""")
        assertEquals(0, shadow.allNotifications.size)
    }

    private class RecordingSpeech : NativeSpeech {
        var text: String? = null
        var language: String? = null
        var rate: Float? = null
        var stopCount = 0

        override fun speak(
            text: String,
            language: String,
            rate: Float,
        ) {
            this.text = text
            this.language = language
            this.rate = rate
        }

        override fun stop() {
            stopCount += 1
        }

        override fun shutdown() = Unit
    }
}

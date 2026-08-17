package ai.omnigent.android

import android.net.Uri
import android.speech.tts.TextToSpeech
import android.webkit.WebView
import androidx.appcompat.app.AppCompatDelegate
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import org.json.JSONObject
import java.util.Locale

internal interface NativeSpeech {
    fun speak(
        text: String,
        language: String,
        rate: Float,
    )

    fun stop()

    fun shutdown()
}

internal class AndroidNativeSpeech(
    context: android.content.Context,
) : NativeSpeech,
    TextToSpeech.OnInitListener {
    private val engine = TextToSpeech(context.applicationContext, this)
    private var ready = false
    private var pending: Triple<String, String, Float>? = null

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (!ready) {
            pending = null
            return
        }
        pending?.let { (text, language, rate) -> speak(text, language, rate) }
        pending = null
    }

    override fun speak(
        text: String,
        language: String,
        rate: Float,
    ) {
        val request = Triple(text.take(MAX_SPEECH_CHARS), language, rate.coerceIn(0.5f, 2f))
        if (!ready) {
            pending = request
            return
        }
        engine.stop()
        engine.language = Locale.forLanguageTag(request.second)
        engine.setSpeechRate(request.third)
        engine.speak(request.first, TextToSpeech.QUEUE_FLUSH, null, "omnigent-conductor")
    }

    override fun stop() {
        pending = null
        engine.stop()
    }

    override fun shutdown() {
        pending = null
        engine.stop()
        engine.shutdown()
    }

    private companion object {
        const val MAX_SPEECH_CHARS = 8_000
    }
}

/**
 * The single web -> native bridge, installed via
 * `WebViewCompat.addWebMessageListener` with an origin allowlist of just the
 * pinned server. Unlike `addJavascriptInterface`, the injected object
 * (`window.`[JS_OBJECT_NAME]`)` is delivered ONLY to frames whose origin
 * matches the allowlist, so a sandboxed / opaque agent-HTML iframe never
 * receives it. We additionally drop non-main-frame messages — together the
 * structural equivalent of the iOS `isMainFrame` + frame-origin check that a
 * raw `addJavascriptInterface` bridge cannot express.
 *
 * [BlobSaver] offloads writes to its own worker.
 */
class OmnigentBridgeListener(
    private val notifications: NativeNotificationManager,
    private val blobSaver: BlobSaver,
    private val speech: NativeSpeech,
) : WebViewCompat.WebMessageListener {
    override fun onPostMessage(
        view: WebView,
        message: WebMessageCompat,
        sourceOrigin: Uri,
        isMainFrame: Boolean,
        replyProxy: JavaScriptReplyProxy,
    ) {
        if (!isMainFrame) return // origin allowlist already gates; defense in depth.
        val data = message.data ?: return
        handle(data)
    }

    /** Parse and dispatch one bridge message; malformed input is dropped. */
    internal fun handle(data: String) {
        val json =
            try {
                JSONObject(data)
            } catch (_: Throwable) {
                return
            }

        when (json.optString("method")) {
            "setColorScheme" -> {
                when (json.optString("scheme")) {
                    "light" -> {
                        AppCompatDelegate.setDefaultNightMode(
                            AppCompatDelegate.MODE_NIGHT_NO,
                        )
                    }

                    "dark" -> {
                        AppCompatDelegate.setDefaultNightMode(
                            AppCompatDelegate.MODE_NIGHT_YES,
                        )
                    }

                    "system" -> {
                        AppCompatDelegate.setDefaultNightMode(
                            AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM,
                        )
                    }
                }
            }

            "setBadgeCount" -> {
                notifications.setBadgeCount(
                    count = json.optInt("count", 0),
                    navigatePath = json.optString("navigatePath").ifEmpty { null },
                    title = json.optString("title").ifEmpty { null },
                    body = json.optString("body").ifEmpty { null },
                )
            }

            "notify" -> {
                val params = json.optJSONObject("params") ?: return
                val title = params.optString("title").ifEmpty { return }
                notifications.notify(
                    title = title,
                    body = params.optString("body").ifEmpty { null },
                    navigatePath = params.optString("navigatePath").ifEmpty { null },
                )
            }

            "speak" -> {
                val params = json.optJSONObject("params") ?: return
                val text = params.optString("text").trim().ifEmpty { return }
                speech.speak(
                    text = text,
                    language = params.optString("language", "en-US"),
                    rate = params.optDouble("rate", 1.0).toFloat(),
                )
            }

            "stopSpeaking" -> speech.stop()

            "blobBase64" -> {
                blobSaver.save(
                    base64 = json.optString("base64").ifEmpty { return },
                    mimeType = json.optString("mimeType").ifEmpty { "application/octet-stream" },
                    suggestedName = json.optString("name"),
                )
            }
        }
    }

    companion object {
        /** Name of the injected transport object as seen from page JS. */
        const val JS_OBJECT_NAME = "omnigentNativeBridge"
    }
}

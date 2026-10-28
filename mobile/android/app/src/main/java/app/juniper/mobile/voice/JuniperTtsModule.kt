package app.juniper.mobile.voice

import android.os.Bundle
import android.speech.tts.TextToSpeech
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import java.util.Locale

/**
 * The Android `TextToSpeech` in pt-BR: the fallback voice when the cloud voice is not available
 * (ADR-023, section 4). The brain sends the text (`voice.say`); this only says it.
 */
class JuniperTtsModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperTts"

  private var tts: TextToSpeech? = null
  @Volatile private var ready = false

  private fun engine(): TextToSpeech {
    tts?.let {
      return it
    }
    val created =
        TextToSpeech(reactContext) { status ->
          ready =
              status == TextToSpeech.SUCCESS &&
                  tts?.setLanguage(Locale("pt", "BR")).let {
                    it != TextToSpeech.LANG_MISSING_DATA && it != TextToSpeech.LANG_NOT_SUPPORTED
                  }
        }
    tts = created
    return created
  }

  /** Resolves true when a pt-BR voice is installed and usable. */
  @ReactMethod
  fun isAvailable(promise: Promise) {
    engine()
    // The engine starts asynchronously: give it a moment, then answer with what we know.
    Thread {
          var waited = 0
          while (!ready && waited < 3000) {
            Thread.sleep(100)
            waited += 100
          }
          promise.resolve(ready)
        }
        .start()
  }

  @ReactMethod
  fun speak(text: String) {
    if (!ready) return
    tts?.speak(text, TextToSpeech.QUEUE_ADD, Bundle(), "juniper-${System.nanoTime()}")
  }

  @ReactMethod
  fun stop() {
    tts?.stop()
  }

  override fun invalidate() {
    tts?.shutdown()
    tts = null
    ready = false
    super.invalidate()
  }
}

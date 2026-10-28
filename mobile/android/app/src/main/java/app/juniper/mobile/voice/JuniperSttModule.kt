package app.juniper.mobile.voice

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule

/**
 * The Android speech recognizer (pt-BR) for the voice chat fallback (ADR-023, section 5): used
 * only when the brain says the cloud STT is out. One utterance per `start`; the JavaScript side
 * restarts it to listen continuously. It replaces a third-party library that no longer builds
 * with current Gradle (it still used jcenter and the old support library).
 *
 * Events: `juniperSttResult` (final text) and `juniperSttError` (the SpeechRecognizer error code,
 * or -1 when there is no recognizer on the phone). `SpeechRecognizer` must be used on the main
 * thread, so every call hops there.
 */
class JuniperSttModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperStt"

  private val main = Handler(Looper.getMainLooper())
  private var recognizer: SpeechRecognizer? = null

  private fun emit(event: String, payload: Any?) {
    if (reactContext.hasActiveReactInstance()) {
      reactContext
          .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
          .emit(event, payload)
    }
  }

  // Required by NativeEventEmitter on the JS side.
  @ReactMethod fun addListener(eventName: String) {}

  @ReactMethod fun removeListeners(count: Int) {}

  @ReactMethod
  fun isAvailable(promise: Promise) {
    promise.resolve(SpeechRecognizer.isRecognitionAvailable(reactContext))
  }

  @ReactMethod
  fun start(locale: String) {
    main.post {
      if (!SpeechRecognizer.isRecognitionAvailable(reactContext)) {
        emit("juniperSttError", -1)
        return@post
      }
      destroyNow()
      val created = SpeechRecognizer.createSpeechRecognizer(reactContext)
      created.setRecognitionListener(
          object : RecognitionListener {
            override fun onResults(results: Bundle?) {
              val text =
                  results
                      ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                      ?.firstOrNull()
                      .orEmpty()
              if (text.isNotBlank()) emit("juniperSttResult", text)
              // No result (silence) is not an error: the JS side just listens again.
              else emit("juniperSttError", SpeechRecognizer.ERROR_NO_MATCH)
            }

            override fun onError(error: Int) = emit("juniperSttError", error)

            override fun onReadyForSpeech(params: Bundle?) {}

            override fun onBeginningOfSpeech() {}

            override fun onRmsChanged(rmsdB: Float) {}

            override fun onBufferReceived(buffer: ByteArray?) {}

            override fun onEndOfSpeech() {}

            override fun onPartialResults(partialResults: Bundle?) {}

            override fun onEvent(eventType: Int, params: Bundle?) {}
          })
      recognizer = created
      created.startListening(
          Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
              .putExtra(
                  RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
              .putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
              .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
              .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1))
    }
  }

  @ReactMethod
  fun stop() {
    main.post { destroyNow() }
  }

  private fun destroyNow() {
    recognizer?.let {
      runCatching { it.cancel() }
      it.destroy()
    }
    recognizer = null
  }

  override fun invalidate() {
    main.post { destroyNow() }
    super.invalidate()
  }
}

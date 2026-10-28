package app.juniper.mobile.media

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.ReadableMap

/**
 * Opens a link and tries to make the music play (ADR-022, section 6).
 *
 * The only code that touches the YouTube Music app, and only from the outside: it opens the link
 * with an `ACTION_VIEW` intent (Android hands it to whichever app handles music.youtube.com) and,
 * once the app is up, sends the *media play* key to the active media session. "Autoplay did not
 * work" is a normal answer, not an error: the link is open and the person taps play.
 *
 * Android 10+ limits starting an activity from the background; when the phone is locked the
 * `startActivity` may be refused. That is reported (`opened: false`, `detail`), never thrown.
 * Behaviour on the screen-off case still has to be checked on the device (docs/pages/mobile.md).
 */
class MediaAutoplayModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperMedia"

  private val audio: AudioManager
    get() = reactContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager

  /**
   * options: waitMs (before the first key, the app needs time to open), attempts, retryMs.
   * Resolves { opened: Boolean, autoplay: Boolean?, detail: String? }; never rejects.
   */
  @ReactMethod
  fun openAndPlay(url: String, options: ReadableMap, promise: Promise) {
    val waitMs = if (options.hasKey("waitMs")) options.getInt("waitMs").toLong() else 3000L
    val attempts = if (options.hasKey("attempts")) options.getInt("attempts") else 3
    val retryMs = if (options.hasKey("retryMs")) options.getInt("retryMs").toLong() else 1500L

    val result = Arguments.createMap()
    val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
      reactContext.startActivity(intent)
    } catch (e: ActivityNotFoundException) {
      result.putBoolean("opened", false)
      result.putNull("autoplay")
      result.putString("detail", "no_app_for_link")
      promise.resolve(result)
      return
    } catch (e: SecurityException) {
      result.putBoolean("opened", false)
      result.putNull("autoplay")
      result.putString("detail", "blocked_by_android")
      promise.resolve(result)
      return
    } catch (e: RuntimeException) {
      result.putBoolean("opened", false)
      result.putNull("autoplay")
      result.putString("detail", "start_failed")
      promise.resolve(result)
      return
    }

    // Wait for the music app to come up and try the play key a few times, off the main thread.
    Thread {
          var playing = false
          try {
            Thread.sleep(waitMs)
            for (attempt in 1..attempts) {
              if (audio.isMusicActive) {
                playing = true
                break
              }
              sendPlayKey()
              Thread.sleep(retryMs)
              if (audio.isMusicActive) {
                playing = true
                break
              }
            }
          } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
          }
          result.putBoolean("opened", true)
          result.putBoolean("autoplay", playing)
          Handler(Looper.getMainLooper()).post { promise.resolve(result) }
        }
        .start()
  }

  private fun sendPlayKey() {
    val down = KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PLAY)
    val up = KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PLAY)
    audio.dispatchMediaKeyEvent(down)
    audio.dispatchMediaKeyEvent(up)
  }
}

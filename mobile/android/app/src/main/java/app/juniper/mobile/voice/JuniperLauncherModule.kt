package app.juniper.mobile.voice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

/**
 * An ongoing notification with a «Falar» button: one tap opens the app on `juniper://voz`, which
 * starts the voice conversation. It is a plain notification (no service, no microphone): the
 * microphone only opens when the app is in front and the person asked.
 */
class JuniperLauncherModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperLauncher"

  private val manager: NotificationManager
    get() = reactContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

  @ReactMethod
  fun show(promise: Promise) {
    try {
      manager.createNotificationChannel(
          NotificationChannel(CHANNEL_ID, "Atalho de voz", NotificationManager.IMPORTANCE_LOW))
      val open =
          PendingIntent.getActivity(
              reactContext,
              0,
              Intent(Intent.ACTION_VIEW, Uri.parse(LAUNCH_URL))
                  .setPackage(reactContext.packageName)
                  .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
              PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
          )
      val notification =
          NotificationCompat.Builder(reactContext, CHANNEL_ID)
              .setSmallIcon(android.R.drawable.ic_btn_speak_now)
              .setContentTitle("Juniper")
              .setContentText("Toque em Falar para conversar")
              .setOngoing(true)
              .setOnlyAlertOnce(true)
              .setContentIntent(open)
              .addAction(0, "Falar", open)
              .build()
      manager.notify(NOTIFICATION_ID, notification)
      promise.resolve(null)
    } catch (e: Exception) {
      promise.reject("launcher_failed", e.message ?: "launcher_failed")
    }
  }

  @ReactMethod
  fun hide(promise: Promise) {
    manager.cancel(NOTIFICATION_ID)
    promise.resolve(null)
  }

  companion object {
    const val CHANNEL_ID = "juniper_launcher"
    const val NOTIFICATION_ID = 4202
    const val LAUNCH_URL = "juniper://voz"
  }
}

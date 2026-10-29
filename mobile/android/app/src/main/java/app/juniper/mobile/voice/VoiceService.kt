package app.juniper.mobile.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.graphics.PixelFormat
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import androidx.core.app.NotificationCompat

/**
 * The foreground service that keeps the voice conversation alive with the screen off
 * (ADR-022, section 5). It does no audio itself: it exists so Android lets the microphone run in
 * the background, and it shows the fixed notification «Juniper está ouvindo» with an end button.
 */
class VoiceService : Service() {

  private var overlay: View? = null

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (intent?.action == ACTION_STOP_SERVICE) {
      removeOverlay()
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return START_NOT_STICKY
    }
    createChannel()
    val title = intent?.getStringExtra(EXTRA_TITLE) ?: "Juniper está ouvindo"
    val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Conversa de voz em andamento"
    val stop =
        PendingIntent.getBroadcast(
            this,
            0,
            Intent(ACTION_END_VOICE).setPackage(packageName),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    val open =
        PendingIntent.getActivity(
            this,
            0,
            packageManager.getLaunchIntentForPackage(packageName),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    val notification: Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Parar", stop)
            .build()
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
    } else {
      startForeground(NOTIFICATION_ID, notification)
    }
    addOverlay()
    return START_NOT_STICKY
  }

  /**
   * A 1x1 transparent window while the conversation is on. Android only lets an app open another
   * app's screen from the background if it has "display over other apps" **and** a visible
   * overlay window; this is that window (no touches, no focus, nothing drawn). It exists only if
   * the person granted the permission, and only while the voice service runs.
   */
  private fun addOverlay() {
    if (overlay != null || !Settings.canDrawOverlays(this)) return
    try {
      val view = View(this)
      val params =
          WindowManager.LayoutParams(
              1,
              1,
              WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
              WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                  WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                  WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
              PixelFormat.TRANSLUCENT,
          )
      params.gravity = Gravity.START or Gravity.TOP
      (getSystemService(Context.WINDOW_SERVICE) as WindowManager).addView(view, params)
      overlay = view
    } catch (e: RuntimeException) {
      overlay = null // the window is a convenience: the conversation works without it
    }
  }

  private fun removeOverlay() {
    overlay?.let {
      try {
        (getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeView(it)
      } catch (e: RuntimeException) {
        // already gone
      }
    }
    overlay = null
  }

  override fun onDestroy() {
    removeOverlay()
    super.onDestroy()
  }

  private fun createChannel() {
    val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    manager.createNotificationChannel(
        NotificationChannel(CHANNEL_ID, "Conversa de voz", NotificationManager.IMPORTANCE_LOW)
    )
  }

  companion object {
    const val CHANNEL_ID = "juniper_voice"
    const val NOTIFICATION_ID = 4201
    const val ACTION_STOP_SERVICE = "app.juniper.mobile.STOP_SERVICE"
    const val ACTION_END_VOICE = "app.juniper.mobile.END_VOICE"
    const val EXTRA_TITLE = "title"
    const val EXTRA_TEXT = "text"
  }
}

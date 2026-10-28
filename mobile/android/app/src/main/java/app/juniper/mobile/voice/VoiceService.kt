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
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * The foreground service that keeps the voice conversation alive with the screen off
 * (ADR-022, section 5). It does no audio itself: it exists so Android lets the microphone run in
 * the background, and it shows the fixed notification «Juniper está ouvindo» with an end button.
 */
class VoiceService : Service() {

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    if (intent?.action == ACTION_STOP_SERVICE) {
      stopForeground(STOP_FOREGROUND_REMOVE)
      stopSelf()
      return START_NOT_STICKY
    }
    createChannel()
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
            .setContentTitle("Juniper está ouvindo")
            .setContentText("Conversa de voz em andamento")
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Encerrar", stop)
            .build()
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
    } else {
      startForeground(NOTIFICATION_ID, notification)
    }
    return START_NOT_STICKY
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
  }
}

package app.juniper.mobile.update

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build

/**
 * The installer's answer to the update session. If the system needs the person to confirm, it
 * sends an intent to open; the other outcomes are reported to the app as a status event. On
 * success the app is replaced and this process ends, so «success» is rarely seen.
 */
class UpdateResultReceiver : BroadcastReceiver() {

  override fun onReceive(context: Context, intent: Intent) {
    val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
    when (status) {
      PackageInstaller.STATUS_PENDING_USER_ACTION -> {
        val confirm =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
            else @Suppress("DEPRECATION") intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
        if (confirm != null) {
          confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
          context.startActivity(confirm)
          UpdateEvents.sink?.invoke("pending_user", "")
        } else {
          UpdateEvents.sink?.invoke("failed", "no_confirmation")
        }
      }
      PackageInstaller.STATUS_SUCCESS -> UpdateEvents.sink?.invoke("success", "")
      else ->
          UpdateEvents.sink?.invoke(
              "failed", intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "status_$status")
    }
  }

  companion object {
    const val ACTION = "app.juniper.mobile.UPDATE_RESULT"
  }
}

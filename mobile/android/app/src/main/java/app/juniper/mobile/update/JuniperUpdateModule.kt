package app.juniper.mobile.update

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Self-update of the app (ADR-026). The brain serves the newest APK inside the private network;
 * this module downloads it straight into an Android `PackageInstaller` session, checks the
 * SHA-256 **before** committing, and hands it to the system installer. Android itself refuses an
 * update that is not signed with the same key as the installed app, so a wrong file can never
 * replace the app. The system asks the person to confirm (once; Android 12+ may skip the question
 * for later updates, which is up to the system).
 */
class JuniperUpdateModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperUpdate"

  @ReactMethod fun addListener(eventName: String) {}

  @ReactMethod fun removeListeners(count: Int) {}

  private fun emit(event: String, status: String, message: String) {
    if (!reactContext.hasActiveReactInstance()) return
    val payload = Arguments.createMap()
    payload.putString("status", status)
    payload.putString("message", message)
    reactContext
        .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
        .emit(event, payload)
  }

  private fun emitProgress(received: Long, total: Long) {
    if (!reactContext.hasActiveReactInstance()) return
    val payload = Arguments.createMap()
    payload.putDouble("received", received.toDouble())
    payload.putDouble("total", total.toDouble())
    reactContext
        .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
        .emit(EVENT_PROGRESS, payload)
  }

  /** The installed version: what the brain's `version_code` is compared with. */
  @ReactMethod
  fun installedVersion(promise: Promise) {
    try {
      val info = reactContext.packageManager.getPackageInfo(reactContext.packageName, 0)
      val code =
          if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode
          else @Suppress("DEPRECATION") info.versionCode.toLong()
      val map = Arguments.createMap()
      map.putDouble("versionCode", code.toDouble())
      map.putString("versionName", info.versionName ?: "")
      promise.resolve(map)
    } catch (e: Exception) {
      promise.reject("version_failed", e.message ?: "version_failed")
    }
  }

  /** Whether Android lets this app install packages ("install unknown apps"). */
  @ReactMethod
  fun canInstall(promise: Promise) {
    promise.resolve(reactContext.packageManager.canRequestPackageInstalls())
  }

  /** Opens the system page where the person allows this app to install updates. */
  @ReactMethod
  fun openInstallSettings(promise: Promise) {
    try {
      val intent =
          Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${reactContext.packageName}"))
              .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      reactContext.startActivity(intent)
      promise.resolve(null)
    } catch (e: Exception) {
      promise.reject("settings_failed", e.message ?: "settings_failed")
    }
  }

  /**
   * Downloads `url` (with the brain's token) into an installer session and commits it. Resolves
   * once the system installer has the file; the final result comes as a `juniperUpdateStatus`
   * event (`pending_user`, `success` or `failed`). Rejects with a short code: `install_permission`,
   * `download_failed`, `size_mismatch`, `checksum_mismatch`, `install_failed`.
   */
  @ReactMethod
  fun downloadAndInstall(url: String, token: String, sha256: String, size: Double, promise: Promise) {
    if (!reactContext.packageManager.canRequestPackageInstalls()) {
      promise.reject("install_permission", "install_permission")
      return
    }
    if (size <= 0 || size > MAX_APK_BYTES) {
      promise.reject("size_mismatch", "bad_size")
      return
    }
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      promise.reject("download_failed", "bad_url")
      return
    }
    UpdateEvents.sink = { status, message -> emit(EVENT_STATUS, status, message) }
    Thread { run(url, token, sha256.lowercase(), size.toLong(), promise) }.start()
  }

  private fun run(url: String, token: String, sha256: String, size: Long, promise: Promise) {
    val installer = reactContext.packageManager.packageInstaller
    var sessionId = -1
    var session: PackageInstaller.Session? = null
    try {
      val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
      params.setAppPackageName(reactContext.packageName)
      params.setSize(size)
      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        // Only honored when this app was the installer of the installed copy (so, from the second
        // self-update on); otherwise the system still asks.
        params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
      }
      sessionId = installer.createSession(params)
      session = installer.openSession(sessionId)

      val connection = URL(url).openConnection() as HttpURLConnection
      connection.connectTimeout = 10_000
      connection.readTimeout = 30_000
      // The token travels in a header: never follow a redirect with it.
      connection.instanceFollowRedirects = false
      connection.setRequestProperty("Authorization", "Bearer $token")
      try {
        if (connection.responseCode != 200) {
          throw UpdateError("download_failed", "http_${connection.responseCode}")
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var received = 0L
        val deadline = System.currentTimeMillis() + DOWNLOAD_DEADLINE_MS
        connection.inputStream.use { input ->
          session.openWrite("juniper.apk", 0, size).use { output ->
            val buffer = ByteArray(64 * 1024)
            var lastEmit = 0L
            while (true) {
              val read = input.read(buffer)
              if (read < 0) break
              received += read
              if (System.currentTimeMillis() > deadline) throw UpdateError("download_failed", "deadline")
              if (received > size) throw UpdateError("size_mismatch", "too_long")
              digest.update(buffer, 0, read)
              output.write(buffer, 0, read)
              if (received - lastEmit >= 512 * 1024) {
                lastEmit = received
                emitProgress(received, size)
              }
            }
            session.fsync(output)
          }
        }
        emitProgress(received, size)
        if (received != size) throw UpdateError("size_mismatch", "short")
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (actual != sha256) throw UpdateError("checksum_mismatch", "sha256")
      } finally {
        connection.disconnect()
      }

      // FLAG_MUTABLE is required here: the system fills in the install status extras. The intent
      // is explicit (our own receiver class), so it cannot be redirected elsewhere.
      val flags =
          PendingIntent.FLAG_UPDATE_CURRENT or
              (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
      val result =
          PendingIntent.getBroadcast(
              reactContext,
              sessionId,
              Intent(reactContext, UpdateResultReceiver::class.java).setAction(UpdateResultReceiver.ACTION),
              flags,
          )
      session.commit(result.intentSender)
      session.close()
      session = null
      promise.resolve(null)
    } catch (e: UpdateError) {
      abandon(installer, session, sessionId)
      promise.reject(e.code, e.detail)
    } catch (e: Exception) {
      abandon(installer, session, sessionId)
      promise.reject("download_failed", e.javaClass.simpleName)
    }
  }

  private fun abandon(installer: PackageInstaller, session: PackageInstaller.Session?, id: Int) {
    try {
      session?.close()
      if (id >= 0) installer.abandonSession(id)
    } catch (ignored: Exception) {
      // already gone
    }
  }

  private class UpdateError(val code: String, val detail: String) : Exception(detail)

  companion object {
    const val EVENT_PROGRESS = "juniperUpdateProgress"
    const val EVENT_STATUS = "juniperUpdateStatus"
    // The brain refuses anything bigger too (brain/clients/app_update.py).
    const val MAX_APK_BYTES = 200L * 1024 * 1024
    const val DOWNLOAD_DEADLINE_MS = 10L * 60 * 1000
  }
}

/** Lets the manifest-registered receiver reach whichever module instance is alive. */
object UpdateEvents {
  @Volatile var sink: ((String, String) -> Unit)? = null
}

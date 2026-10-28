package app.juniper.mobile.voice

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioAttributes
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.modules.core.DeviceEventManagerModule
import java.util.concurrent.LinkedBlockingQueue

/**
 * Microphone capture and playback for the voice chat (ADR-022 / ADR-023).
 *
 * Capture uses the VOICE_COMMUNICATION source, which turns on the platform's echo cancellation
 * and noise suppression (the effects are also requested explicitly when the device has them);
 * 16-bit mono PCM goes to JavaScript in chunks of ~100 ms as base64. Playback is an AudioTrack in
 * streaming mode with the same voice-communication usage, so the echo canceller knows what is
 * being played. `flush` drops everything queued at once (she was cut off).
 *
 * Neither the capture nor the player decides anything: the brain does (voice activity, turns).
 */
class JuniperAudioModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

  override fun getName(): String = "JuniperAudio"

  @Volatile private var recording = false
  private var captureRate = 0
  private var record: AudioRecord? = null
  private var captureThread: Thread? = null
  private var effects: List<android.media.audiofx.AudioEffect> = emptyList()

  private var track: AudioTrack? = null
  private var playQueue = LinkedBlockingQueue<ByteArray>()
  private var playThread: Thread? = null
  @Volatile private var playing = false

  private var stopReceiver: BroadcastReceiver? = null
  private var deviceCallback: AudioDeviceCallback? = null

  private val audioManager: AudioManager
    get() = reactContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager

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

  /**
   * Starts the foreground service alone (no capture): the phone waiting for the wake word keeps
   * the microphone permission in the background through it. The notification says what it is.
   */
  @ReactMethod
  fun startService(title: String, text: String, promise: Promise) {
    try {
      ContextCompat.startForegroundService(
          reactContext,
          Intent(reactContext, VoiceService::class.java)
              .putExtra(VoiceService.EXTRA_TITLE, title)
              .putExtra(VoiceService.EXTRA_TEXT, text))
      registerStopReceiver()
      promise.resolve(null)
    } catch (e: Exception) {
      promise.reject("service_start_failed", e.message ?: "service_start_failed")
    }
  }

  @ReactMethod
  fun stopService(promise: Promise) {
    reactContext.startService(
        Intent(reactContext, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SERVICE))
    unregisterStopReceiver()
    promise.resolve(null)
  }

  @ReactMethod
  fun start(sampleRate: Int, promise: Promise) {
    if (recording) {
      promise.resolve(null)
      return
    }
    try {
      ContextCompat.startForegroundService(
          reactContext, Intent(reactContext, VoiceService::class.java))
      registerStopReceiver()
      watchDevices()
      beginCapture(sampleRate)
      promise.resolve(null)
    } catch (e: Exception) {
      stopCapture()
      promise.reject("audio_start_failed", e.message ?: "audio_start_failed")
    }
  }

  /**
   * Releases the microphone without ending the session (the service and the player stay): the
   * speech recognizer fallback needs the microphone to itself while the cloud STT is out.
   */
  @ReactMethod
  fun pauseCapture(promise: Promise) {
    stopCapture()
    promise.resolve(null)
  }

  @ReactMethod
  fun resumeCapture(promise: Promise) {
    if (recording || captureRate == 0) {
      promise.resolve(null)
      return
    }
    try {
      beginCapture(captureRate)
      promise.resolve(null)
    } catch (e: Exception) {
      stopCapture()
      promise.reject("audio_resume_failed", e.message ?: "audio_resume_failed")
    }
  }

  private fun beginCapture(sampleRate: Int) {
    captureRate = sampleRate
    val channel = AudioFormat.CHANNEL_IN_MONO
    val encoding = AudioFormat.ENCODING_PCM_16BIT
    val chunkBytes = sampleRate / 10 * 2 // 100 ms
    val minBuffer = AudioRecord.getMinBufferSize(sampleRate, channel, encoding)
    val recorder =
        AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRate,
            channel,
            encoding,
            maxOf(minBuffer, chunkBytes * 4),
        )
    if (recorder.state != AudioRecord.STATE_INITIALIZED) {
      recorder.release()
      throw IllegalStateException("microphone_unavailable")
    }
    effects = buildList {
      if (AcousticEchoCanceler.isAvailable())
          AcousticEchoCanceler.create(recorder.audioSessionId)?.let {
            it.enabled = true
            add(it)
          }
      if (NoiseSuppressor.isAvailable())
          NoiseSuppressor.create(recorder.audioSessionId)?.let {
            it.enabled = true
            add(it)
          }
      if (AutomaticGainControl.isAvailable())
          AutomaticGainControl.create(recorder.audioSessionId)?.let {
            it.enabled = true
            add(it)
          }
    }
    record = recorder
    recorder.startRecording()
    recording = true
    captureThread =
        Thread {
              val buffer = ByteArray(chunkBytes)
              while (recording) {
                var filled = 0
                while (recording && filled < chunkBytes) {
                  val n = recorder.read(buffer, filled, chunkBytes - filled)
                  if (n <= 0) break
                  filled += n
                }
                if (recording && filled == chunkBytes) {
                  emit("juniperAudioChunk", Base64.encodeToString(buffer, Base64.NO_WRAP))
                }
              }
            }
            .also {
              it.name = "juniper-capture"
              it.start()
            }
  }

  /** True when something that is not the phone's own speaker is plugged in (wired or Bluetooth). */
  private fun headsetConnected(): Boolean =
      audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any {
        when (it.type) {
          AudioDeviceInfo.TYPE_WIRED_HEADSET,
          AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
          AudioDeviceInfo.TYPE_USB_HEADSET,
          AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
          AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> true
          else -> false
        }
      }

  @ReactMethod
  fun hasHeadset(promise: Promise) {
    promise.resolve(headsetConnected())
  }

  /**
   * Plays her voice on the loudspeaker (`speaker = true`) or on whatever is plugged in. The
   * communication mode is what makes the platform echo canceller work and lets the voice use the
   * loudspeaker instead of the earpiece. The cérebro also ignores the microphone while she speaks
   * on the loudspeaker (half-duplex), so her own voice never becomes a question.
   */
  @ReactMethod
  fun applyRoute(speaker: Boolean) {
    val manager = audioManager
    manager.mode = AudioManager.MODE_IN_COMMUNICATION
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
      if (speaker) {
        manager.availableCommunicationDevices
            .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
            ?.let { manager.setCommunicationDevice(it) }
      } else {
        manager.clearCommunicationDevice()
      }
    } else {
      manager.isSpeakerphoneOn = speaker // deprecated since API 31, still the way before it
    }
  }

  private fun resetRoute() {
    val manager = audioManager
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
      manager.clearCommunicationDevice()
    } else {
      manager.isSpeakerphoneOn = false
    }
    manager.mode = AudioManager.MODE_NORMAL
  }

  /** Tells JavaScript when headphones are plugged in or out, so the route can follow. */
  private fun watchDevices() {
    if (deviceCallback != null) return
    val callback =
        object : AudioDeviceCallback() {
          override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>?) {
            emit("juniperAudioRoute", headsetConnected())
          }

          override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) {
            emit("juniperAudioRoute", headsetConnected())
          }
        }
    audioManager.registerAudioDeviceCallback(callback, Handler(Looper.getMainLooper()))
    deviceCallback = callback
  }

  private fun unwatchDevices() {
    deviceCallback?.let { audioManager.unregisterAudioDeviceCallback(it) }
    deviceCallback = null
  }

  /** Ends the capture and the route. `keepService`: the wake word keeps waiting in the service. */
  @ReactMethod
  fun stop(keepService: Boolean, promise: Promise) {
    stopCapture()
    captureRate = 0
    unwatchDevices()
    resetRoute()
    if (!keepService) {
      reactContext.startService(
          Intent(reactContext, VoiceService::class.java)
              .setAction(VoiceService.ACTION_STOP_SERVICE))
      unregisterStopReceiver()
    }
    promise.resolve(null)
  }

  private fun stopCapture() {
    recording = false
    captureThread?.interrupt()
    captureThread = null
    effects.forEach { runCatching { it.release() } }
    effects = emptyList()
    record?.let {
      runCatching { it.stop() }
      it.release()
    }
    record = null
  }

  private fun registerStopReceiver() {
    if (stopReceiver != null) return
    val receiver =
        object : BroadcastReceiver() {
          override fun onReceive(context: Context?, intent: Intent?) {
            emit("juniperStopRequested", null)
          }
        }
    val filter = IntentFilter(VoiceService.ACTION_END_VOICE)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
      reactContext.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
    } else {
      @Suppress("UnspecifiedRegisterReceiverFlag") reactContext.registerReceiver(receiver, filter)
    }
    stopReceiver = receiver
  }

  private fun unregisterStopReceiver() {
    stopReceiver?.let { runCatching { reactContext.unregisterReceiver(it) } }
    stopReceiver = null
  }

  @ReactMethod
  fun openPlayer(sampleRate: Int, promise: Promise) {
    try {
      closePlayerInternal()
      val minBuffer =
          AudioTrack.getMinBufferSize(
              sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
      val audioTrack =
          AudioTrack.Builder()
              .setAudioAttributes(
                  AudioAttributes.Builder()
                      .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                      .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                      .build())
              .setAudioFormat(
                  AudioFormat.Builder()
                      .setSampleRate(sampleRate)
                      .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                      .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                      .build())
              .setBufferSizeInBytes(maxOf(minBuffer, sampleRate / 5 * 2))
              .setTransferMode(AudioTrack.MODE_STREAM)
              .build()
      audioTrack.play()
      track = audioTrack
      playQueue = LinkedBlockingQueue()
      playing = true
      playThread =
          Thread {
                val queue = playQueue
                while (playing) {
                  val chunk = try { queue.take() } catch (e: InterruptedException) { break }
                  if (!playing) break
                  audioTrack.write(chunk, 0, chunk.size)
                }
              }
              .also {
                it.name = "juniper-playback"
                it.start()
              }
      promise.resolve(null)
    } catch (e: Exception) {
      promise.reject("player_failed", e.message ?: "player_failed")
    }
  }

  @ReactMethod
  fun play(base64Pcm: String) {
    if (!playing) return
    runCatching { playQueue.put(Base64.decode(base64Pcm, Base64.NO_WRAP)) }
  }

  @ReactMethod
  fun flush() {
    playQueue.clear()
    track?.let {
      runCatching {
        it.pause()
        it.flush()
        it.play()
      }
    }
  }

  @ReactMethod
  fun closePlayer(promise: Promise) {
    closePlayerInternal()
    promise.resolve(null)
  }

  private fun closePlayerInternal() {
    playing = false
    playThread?.interrupt()
    playThread = null
    playQueue.clear()
    track?.let {
      runCatching { it.stop() }
      it.release()
    }
    track = null
  }

  override fun invalidate() {
    unwatchDevices()
    stopCapture()
    closePlayerInternal()
    unregisterStopReceiver()
    super.invalidate()
  }
}

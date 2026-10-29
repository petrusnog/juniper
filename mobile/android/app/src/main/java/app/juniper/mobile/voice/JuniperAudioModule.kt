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
import android.media.AudioFocusRequest
import android.media.AudioRecord
import android.media.AudioRouting
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
import java.util.concurrent.TimeUnit

/**
 * Microphone capture and playback for the voice chat (ADR-022 / ADR-023).
 *
 * Capture uses the VOICE_COMMUNICATION source, which turns on the platform's echo cancellation
 * and noise suppression (the effects are also requested explicitly when the device has them);
 * 16-bit mono PCM goes to JavaScript in chunks of ~100 ms as base64. Playback is an AudioTrack in
 * streaming mode with the media usage, sent to a Bluetooth device, wired headphones or the
 * loudspeaker, **never the earpiece** (see `pickOutput`), and it ducks other media while she
 * speaks (audio focus). `flush` drops everything queued at once (she was cut off).
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
  private var routingGuard: AudioRouting.OnRoutingChangedListener? = null
  @Volatile private var focusRequest: AudioFocusRequest? = null

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

  /**
   * Where her voice goes. Rule, with no exception: **never the earpiece** (the speaker held to the
   * ear). Order: a Bluetooth audio device (the car's speakers), then wired or USB headphones, then
   * the phone's own loudspeaker. The earpiece is not even a candidate.
   */
  private fun pickOutput(): AudioDeviceInfo? {
    val all =
        audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).filter {
          it.type != AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
        }
    fun first(vararg types: Int) = all.firstOrNull { it.type in types }
    val bleTypes =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            intArrayOf(AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER)
        else intArrayOf()
    return first(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)
        ?: first(*bleTypes)
        ?: first(AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
        ?: first(
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_USB_HEADSET,
        )
        ?: first(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)
  }

  /**
   * True only for wired or USB headphones: the ear is closed, so the microphone may stay open
   * while she speaks (the person can talk over her). Bluetooth counts as a *speaker* (it is
   * usually the car): the microphone would hear it, so the brain keeps it closed while she talks.
   */
  private fun closedEarOutput(): Boolean =
      when (pickOutput()?.type) {
        AudioDeviceInfo.TYPE_WIRED_HEADSET,
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
        AudioDeviceInfo.TYPE_USB_HEADSET -> true
        else -> false
      }

  @ReactMethod
  fun hasHeadset(promise: Promise) {
    promise.resolve(closedEarOutput())
  }

  /**
   * Sends her voice to [pickOutput]. A2DP and wired outputs use the normal audio mode (so the
   * media route and volume apply, and the car speaks in stereo); only a Bluetooth "hands-free"
   * (SCO) device needs the communication mode. In every case the earpiece is excluded.
   */
  private fun enforceOutput() {
    val manager = audioManager
    val target = pickOutput()
    val needsCommunicationMode = target?.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
    manager.mode =
        if (needsCommunicationMode) AudioManager.MODE_IN_COMMUNICATION else AudioManager.MODE_NORMAL
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
      if (needsCommunicationMode && target != null) {
        manager.setCommunicationDevice(target)
      } else {
        manager.clearCommunicationDevice()
      }
    } else {
      // Before API 31 the only safe choice outside A2DP/wired is the loudspeaker.
      manager.isSpeakerphoneOn = target?.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
    }
    if (target != null) {
      track?.setPreferredDevice(target)
    }
  }

  /** Kept for the JavaScript side: the choice is the same whatever the route says. */
  @ReactMethod
  fun applyRoute(speaker: Boolean) {
    enforceOutput()
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
            enforceOutput() // a car or headset appeared: follow it (never the earpiece)
            emit("juniperAudioRoute", closedEarOutput())
          }

          override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) {
            enforceOutput()
            emit("juniperAudioRoute", closedEarOutput())
          }
        }
    audioManager.registerAudioDeviceCallback(callback, Handler(Looper.getMainLooper()))
    deviceCallback = callback
  }

  private fun unwatchDevices() {
    deviceCallback?.let { audioManager.unregisterAudioDeviceCallback(it) }
    deviceCallback = null
  }

  @ReactMethod
  fun stop(promise: Promise) {
    stopCapture()
    captureRate = 0
    unwatchDevices()
    resetRoute()
    reactContext.startService(
        Intent(reactContext, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SERVICE))
    unregisterStopReceiver()
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
                      .setUsage(AudioAttributes.USAGE_MEDIA)
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
      track = audioTrack
      enforceOutput()
      // Hard guarantee: if the system ever moves her voice to the earpiece, move it back.
      val guard =
          AudioRouting.OnRoutingChangedListener { routing ->
            if ((routing as? AudioTrack)?.routedDevice?.type ==
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE) {
              enforceOutput()
            }
          }
      audioTrack.addOnRoutingChangedListener(guard, Handler(Looper.getMainLooper()))
      routingGuard = guard
      audioTrack.play()
      playQueue = LinkedBlockingQueue()
      playing = true
      playThread =
          Thread {
                val queue = playQueue
                while (playing) {
                  // Other media (the music app) is ducked while she speaks, and gets its volume
                  // back a moment after she stops.
                  val chunk =
                      try {
                        queue.poll(FOCUS_RELEASE_AFTER_MS, TimeUnit.MILLISECONDS)
                      } catch (e: InterruptedException) {
                        break
                      }
                  if (!playing) break
                  if (chunk == null) {
                    abandonFocus()
                    continue
                  }
                  requestFocus()
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
      routingGuard?.let { guard -> runCatching { it.removeOnRoutingChangedListener(guard) } }
      runCatching { it.stop() }
      it.release()
    }
    routingGuard = null
    track = null
    abandonFocus()
  }

  /** Asks the system to lower other media while she speaks (a short, "may duck" focus). */
  private fun requestFocus() {
    if (focusRequest != null) return
    val request =
        AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build())
            .setOnAudioFocusChangeListener {}
            .build()
    if (audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
      focusRequest = request
    }
  }

  private fun abandonFocus() {
    focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
    focusRequest = null
  }

  companion object {
    /** How long after the last chunk the other media gets its volume back. */
    private const val FOCUS_RELEASE_AFTER_MS = 1200L
  }

  override fun invalidate() {
    unwatchDevices()
    stopCapture()
    closePlayerInternal()
    unregisterStopReceiver()
    super.invalidate()
  }
}

package app.juniper.mobile.media

import app.juniper.mobile.update.JuniperUpdateModule
import app.juniper.mobile.voice.JuniperAudioModule
import app.juniper.mobile.voice.JuniperLauncherModule
import app.juniper.mobile.voice.JuniperSttModule
import app.juniper.mobile.voice.JuniperTtsModule
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.uimanager.ViewManager

/** Registers the app's own native modules (autoplay, microphone and playback, device TTS, voice shortcut, self-update). */
class JuniperPackage : ReactPackage {
  override fun createNativeModules(reactContext: ReactApplicationContext): List<NativeModule> =
      listOf(
          MediaAutoplayModule(reactContext),
          JuniperAudioModule(reactContext),
          JuniperTtsModule(reactContext),
          JuniperSttModule(reactContext),
          JuniperLauncherModule(reactContext),
          JuniperUpdateModule(reactContext),
      )

  override fun createViewManagers(
      reactContext: ReactApplicationContext
  ): List<ViewManager<*, *>> = emptyList()
}

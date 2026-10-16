# TikTok LIVE Monitor for Android

Arabic RTL dashboard for monitoring TikTok LIVE accounts from an Android phone.

## Current build

- Package: `com.amer.tiktokmonitor`
- Version: `1.0` (debug build)
- Minimum Android version: Android 7.0 (API 24)
- Architectures included: ARM64, ARMv7, x86_64
- Runtime: embedded Python 3.11 + TikTokLive 7.0.1
- The dashboard and account list run locally on the phone; live event data requires an internet connection.

## Install the APK

1. Open the latest successful [Android build workflow](https://github.com/ameralbasser/TikTokMonitor-Android/actions/workflows/android.yml).
2. Open the latest successful run.
3. Under **Artifacts**, download `TikTokMonitor-debug-apk`.
4. Extract the ZIP and install `app-debug.apk` on the phone. Android may ask you to allow installation from the app used to download the file.
5. Launch **TikTok LIVE Monitor**, add a TikTok username without the `@`, and start monitoring while that account is LIVE.

The workflow artifact is temporary and expires after 14 days. Download a copy you want to keep.

## Automated checks

GitHub Actions validates Python syntax, checks that Android entry-point files exist, and assembles the debug APK. A successful build confirms compilation and packaging, but it does not replace a runtime test on a physical Android device.

## Notes and limitations

- TikTok may change its undocumented LIVE interfaces; event availability depends on what TikTok sends to the client.
- A falling viewer count does not always include an individual viewer-leave event, so individual departures cannot be guaranteed in every LIVE.
- Video recording/validation depends on an FFmpeg executable. This Android build does not bundle a separate FFmpeg binary, so video recording may not work on Android yet; event monitoring is separate from video recording.
- This is a debug APK, not a Play Store release. It is not signed with a production release key.
- Keep the app in the foreground while first testing. Android may pause network activity if the app is backgrounded or the device enters battery-saving mode.

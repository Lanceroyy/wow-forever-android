# Build, install and upgrade

The branch is intended for local evaluation. There is no published hardened APK or release. Do not enter a real Battle.net password until the device and native supply-chain checks in the [audit](SECURITY_AUDIT.md) are addressed.

1. Check out `security/hardening-v1`. Use JDK 17, Android SDK 36, Build Tools 35.0.0 and NDK 27.3.13750724. Set `sdk.dir` in untracked `local.properties` and accept Android SDK licenses using the normal Android tools.
2. From a shell with Bash and curl, run `bash tools/fetch_components.sh`. The Gradle build rejects missing, changed or unexpected bundle files. Run `./gradlew :app:testModernDebugUnitTest :app:assembleModernDebug` (on Windows use `gradlew.bat`). The normal output is `app/build/outputs/apk/modern/debug/app-modern-debug.apk`. Check the reported SHA-256 and use `apksigner verify --print-certs <apk>` before sideloading.
3. The package ID stays `app.wowforever`. Android requires the original signing key for an in-place upgrade. A local debug key will normally differ from the upstream release key. Use a spare device/profile or back up your launcher settings and shared WoW folder first. An uninstall removes private launcher/container data; verify game files and backup location independently before uninstalling. Do not use signature bypasses. A debug build is not a release signing identity.
4. If using an older version with saved login, remove `_forever_/login.txt` and `_classic_beta_/login.txt` from every prior game root, including SD cards and backups, without opening or copying the contents. The branch only cleans reachable configured roots. Review old crash reports and diagnostic copies from builds that logged input. If the password was saved in the old launcher or typed through its logging IME path, rotate it through Blizzard, review account sessions and enable the Authenticator.
5. Select the existing WoW Forever directory or install through the launcher. On Android 11+ shared roots still need all-files access. Choose **Log in manually** and use the game's own sign-in UI. If WoW has no usable login screen, stop the test and report the build and device; the launcher has no automatic fallback. The launcher does not need microphone permission. Secure windows can suppress recording and nonsecure display output.
6. If authenticated Blizzard metadata fails, check the connection and retry. For an older copied install with only HTTP CDN entries, the first launch needs Blizzard HTTPS so it can replace the old routes. Do not re-enable HTTP. To copy desktop CASC data over USB, use the optional sync script with a trusted source folder. It does not install executable files.

The in-app updater only offers fork releases with a GitHub SHA-256 digest and the same signing certificate set as the installed app. It also requires a higher Android version code. After granting permission to install from this app, return to the update screen and select install; Android displays its own confirmation. The current fork has no published update.

## Separate-UID device probe (Android 11+)

Use a test device. Build with `./gradlew -PsecurityProbe=true :app:assembleModernDebug :app:assembleModernDebugAndroidTest :security-probe:assembleDebug`. Install the debug launcher and probe with adb. Run `:app:connectedModernDebugAndroidTest` for the manifest and preference tests. The probe uses no internet and runs in its own app UID. Example shell commands below use only synthetic one-byte files:

```sh
adb shell appops set app.wowforever.securityprobe MANAGE_EXTERNAL_STORAGE allow
adb shell 'printf S > /sdcard/Download/wow-security-shared-canary'
adb shell "run-as app.wowforever sh -c 'printf P > files/wow-security-private-canary'"
adb shell run-as app.wowforever pwd
adb shell run-as app.wowforever test -f files/wow-security-private-canary
adb shell am instrument -w \
  -e shared /sdcard/Download/wow-security-shared-canary \
  -e private /data/user/0/app.wowforever/files/wow-security-private-canary \
  app.wowforever.securityprobe/.StorageProbe
adb shell run-as app.wowforever rm -f files/wow-security-private-canary
adb shell rm -f /sdcard/Download/wow-security-shared-canary
adb shell appops set app.wowforever.securityprobe MANAGE_EXTERNAL_STORAGE default
```

Use the actual absolute data directory printed by `run-as ... pwd` for the private path. Verify the shared canary is readable and the known-existing private one is inaccessible. The probe tests the Android isolation boundary with a synthetic file; the launcher has no credential file to probe. Also run the clean-install and upgrade flows with no account, including cancellation, process kill, a stale synthetic legacy handoff, external display/IME, controller/touch, resolution and region changes, SD storage, and background/resume. Inspect both root folders and test logcat for synthetic canaries. Test a wrong-signer APK on a disposable device; never relax the installer checks.

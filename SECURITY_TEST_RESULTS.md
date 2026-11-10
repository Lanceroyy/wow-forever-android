# Security verification record

Baseline: v2.3.3 (`f13da6796612146c73bcf5c0758a3a75c01080df`). Verification date: 2026-11-09. Host: Windows, JDK 17, Gradle 8.12.1. Android API 36, Build Tools 35.0.0 and NDK 27.3.13750724 were installed in the checkout from official archives and checked against Google SDK metadata. No Battle.net account or real credentials were used. No release signing configuration was present.

| Check | Result and evidence |
|---|---|
| Official Blizzard transport and data | **PASS (host).** Both `us.version.battle.net/v2/products/wowf` endpoints returned HTTP 200 over HTTPS. The observed US row advertised both legacy HTTP and official HTTPS; the launcher keeps only allowlisted HTTPS routes. Both allowed CDN hosts served the build config; the primary CDN returned exact 206 ranges. |
| Live CASC client install | **PASS (host).** Ran the production Kotlin downloader against build `1.60.1.70862` in a disposable directory seeded with copies of public index files. It verified the 1,404 archive indexes and one loose-file index, fetched authenticated encoding pages and verified six client files. `Wow-ARM64.exe` was 80,315,600 bytes. A second run repaired an intentionally modified copy of the executable and cleared the update marker. No game executable was launched. |
| Focused security JVM tests | **PASS.** 39 tests, 0 failures/errors/skips, using the production Kotlin security sources with lightweight Android shims for the independent host runner. Covered legacy handoff cleanup and symlinks, HTTPS/invalid TLS/redirects/interrupted bodies, path and CASC corruption, APK digests, identity checks, and existing build-record compatibility. |
| Four bundled archives | **PASS.** SHA-256 matches all four pins. Three also match the upstream GitHub release digest. Verified the fourth against the project pin. Shell syntax, cached fetch and `--verify-only` checks passed; Gradle's prebuild pin check passed. A read-only listing confirmed the expected Proton/DXVK/Turnip file layout and 15 Proton symlinks with in-tree targets. A tampered input was rejected without a published archive or a leftover temporary file. |
| Gradle wrapper | **PASS.** Checked the distribution SHA-256 and wrapper JAR against the Gradle project, pinned the distribution SHA-256. |
| Modern Android assembly | **PASS.** `:app:assembleModernDebug` and `:app:assembleModernDebugAndroidTest` assembled. Build Tools 35.0.0 `apksigner verify --verbose` accepted all three artifacts with APK Signature Scheme v2. The launcher APK identifies as app.wowforever 2.3.3, ARM64, target API 36. Packaged permissions omit RECORD_AUDIO; merged application denies cleartext, exposes only the launcher alias among activities, and protects the AndroidX profile installer receiver with DUMP. |
| Full modern JVM suite (Windows) | **FAIL.** 367 tests discovered: 355 passed, 10 failed, 2 skipped. Failures were in `ContainerOverlayFilesTest` (7), `SharedComponentsTest` (2), and `ImageFsInstallerTest` (1). Windows rejected Wine drive filenames containing a colon and Unix file attributes; other failures compared symlink/prefix paths. Rerunning the four affected classes at the exact upstream commit discovered 49 tests and reproduced the same 10 failures and 2 skips, by test name. |
| Android lint | **PASS.** Modern debug lint reported 0 errors, 567 warnings and 9 informational entries. Fixed the inactive microphone bridge, Android 10 display API use, optional TV/touchscreen declarations, and literal ALSA/Vulkan constants flagged on the first run. |
| GitHub Actions bootstrap | The first hosted run stopped at missing sdkmanager. The workflow now installs Google command-line tools 13.0 from an HTTPS archive pinned by SHA-256, then installs the same Android platform and NDK used locally. The archive length and digest were checked against Google SDK metadata before recording the SHA-256. |
| Separate storage probe package | **PASS (assembly); NOT RUN (device).** The optional probe builds as an ordinary separate app. ADB showed no attached Android device. |
| Credential isolation, permission behavior and real APK signature tests | **NOT RUN (device).** Run the synthetic-canary protocol and instrumentation tests in [the installation guide](SECURITY_INSTALL.md). Host tests cover legacy file cleanup, URL policies, APK identity decisions and checksum handling; they do not demonstrate Android sandbox behavior or package installer behavior. |
| Gameplay compatibility | **NOT RUN (device).** Need Snapdragon 8/Adreno 740 and 8 Elite/Adreno 8xx, internal and removable storage, manual login, guest network/streaming, graphics, touch/controller, resolution and region controls, and Authenticator background/resume tests. No secure shadow root or automatic login has been demonstrated. |
| Release build | **NOT BUILT.** No release signing configuration; no release/APK publication. |

## Local artifacts (debug keys)

| APK | Bytes | SHA-256 |
|---|---:|---|
| `app/build/outputs/apk/modern/debug/app-modern-debug.apk` | 219946350 | `fec03c29abea0c03b5853c115911ec2337fe44ee684951101ca92a1ab7514add` |
| `app/build/outputs/apk/androidTest/modern/debug/app-modern-debug-androidTest.apk` | 8604547 | `d845f2b9f67b68b4401700bf0fb37809101874ed43d11e4da52f96ef7343eb42` |
| `tools/security-probe/build/outputs/apk/debug/security-probe-debug.apk` | 8893 | `095b11d476677e1b96ed452a984879233938d221190651ccbbb68e25503dd4ca` |


These checks establish the behavior of the tested launcher paths. They do not establish provenance of closed/prebuilt code, authenticate all runtime GameNative archives or prove that an existing shared game folder is safe from local tampering. Do not use a real account for the initial device tests.

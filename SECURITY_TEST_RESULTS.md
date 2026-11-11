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

## Galaxy startup follow-up (2026-11-10)

No Battle.net credentials were entered. The endpoint for this smoke test is the visible Battle.net login screen.

| Device | Observed result |
|---|---|
| Galaxy S23, Android 15 | **Installed; login unverified.** The protected debug APK showed the Android splash, then the lab capture went black on entering the protected activity. A sample app and the Android home screen remained visible in the control session. The original APK session ended after about two minutes and retained no media or crash trace. In the auto-start probe, the lab APK verified the game files in its app-specific storage without requesting broad storage access and reached 95% of Bionic installation just before cutoff. The inspected app logs contained no fatal exception or fatal signal through that stage. |
| Galaxy S23+, Android 13 | **Game rendered; login unverified.** A capture-enabled lab APK verified the Blizzard client install (`1.60.1.70888`), installed Bionic, launched with Vulkan, and rendered the photosensitivity notice and intro cinematic. The game showed a black background with the controller overlay during first-run setup before the notice appeared. The session expired during the cinematic. An intro-skip build reached runtime setup before another cutoff. |
| Galaxy S25, Android 15 | **First-run game setup rendered; login unverified.** The lab APK automatically selected OpenGL. It installed the game and runtime and rendered the game's initial file-setup dialog before the session expired. |
| Galaxy S25 Ultra, Android 15 | **Client installed; login unverified.** The lab APK automatically selected OpenGL, verified the Blizzard client install, and started runtime setup before the session expired. |
| Galaxy S25+, Android 16 | **Blocked before install.** After Samsung sign-in, Samsung Remote Test Lab reserved physical S25+ devices in the UK and Poland. Both attempts reported `Device used by another client` without opening an accessible device stream. Reconnecting after the UK phone restarted produced the same error. Returned the Poland reservation. Samsung disabled early return for the remaining UK reservation. Neither device received an APK. |

The focused modern debug JVM run passed all 46 tests across launcher configuration, renderer selection, handoff cleanup, downloader and updater. The capture and auto-start variants are lab-only and must not be used for real sign-in. The rebuilt protected debug APK retains Android screen-capture protection and has SHA-256 `ade9b1a6271ee757dc4e1d11733ded0d7cfd81a9f0bd192f534fd612f5aab9f8`.

The protected APK passed the Android SDK `zipalign -c -P 16 4` check. All 27 packaged ARM64 shared libraries have ELF load segments aligned to at least 16 KiB. This is a host packaging check. The test still needs to exercise the game runtime on a physical device.

## Headless Android 16 emulator

The protected APK installed and opened on the Google APIs x86-64 image with ARM64 translation. All three `LauncherSecurityTest` instrumentation tests passed. The launcher upgraded a credential-free test installation to Blizzard client `1.60.1.70888` and started its X11 environment with OpenGL selected.

The stock launch stopped before the game window. Android's `/system/bin/linker64` rejected the ARM64 Wine and PulseAudio executables because the linker is x86-64. The Android crash buffer also recorded a null device handle in `GPUHelper.vkGetApiVersion` inside `libwinlator_11.so`. A separate ARM64 test executable ran directly and via `/system/bin/arm64/linker64`, which isolated the stock linker's architecture mismatch.

The virtual GPU checks showed SwiftShader at Vulkan 1.2. NVIDIA host graphics and Lavapipe both exposed Vulkan 1.3 in the guest but omitted `VK_EXT_robustness2`. The Battle.net login screen remains unverified. Both headless instances and their dedicated ADB server were stopped after testing.

## Virtual-only startup follow-up (2026-11-11)

| Test | Observed result |
|---|---|
| Android 16 x86-64 with ARM64 translation and an emulator-only WineD3D build | **Game launch failed.** The alternate ARM64 linker ran `wine --version` and returned `wine-11.0`. The normal launch still stopped when the x86-64 translation runner encountered the ARM64 `LD_PRELOAD` library. With those environment variables removed, a separate `wine cmd /c ver` probe failed because the Wine preloader could not reserve 2,099,269,632 bytes at `0x200000`. Bypassing the initial re-exec for this probe advanced to wineserver, but the child wineboot hit the same reservation failure. The lab Vulkan check did not reproduce the original Android app crash. |
| Android 16 native ARM64 guest under software CPU emulation on Windows | **Blocked before install.** Google SDK image revision 7 matched the repository metadata checksum and the guest reported ABI `arm64-v8a`. The SDK's ARM64 QEMU process froze at SurfaceFlinger initialization with host graphics and SwiftShader. With guest rendering it exited with Windows code `0xC0000005` at the same stage. Single-threaded CPU emulation with reduced host graphics features also froze. None of these guests reached Android's package-manager readiness. |
| Android 13 native ARM64 guest under software CPU emulation on Windows | **Blocked before install.** Google SDK image revision 17 matched the repository metadata checksum, and generated QEMU paths confirmed the Android 13 kernel, system and vendor images. The QEMU process exited with Windows code 0xC0000005 during hardware-service startup on the default graphics pipe. Switching to the address-space transport or to the VirtIO GPU pipe left the emulator stalled with ADB offline. The VirtIO GPU run confirmed the kernel detected the PCI graphics device. The lab APK never installed on the native ARM64 guest. |

The signed lab APKs used temporary capture and compatibility changes for the virtual test. The protected debug APK still matches SHA-256 `ade9b1a6271ee757dc4e1d11733ded0d7cfd81a9f0bd192f534fd612f5aab9f8`. No Battle.net credentials were entered.
All isolated emulator processes and the dedicated ADB server were stopped after the tests. The Battle.net login screen remains unverified.

## Linux VPS virtual guest (2026-11-11)

The authorized VPS runs Ubuntu 24.04 on an x86-64 Ryzen 7 PRO 1700X. KVM cannot accelerate an ARM64 guest on this CPU. The lab used Google SDK archives verified against repository metadata, software CPU emulation, Lavapipe graphics and a dedicated loopback ADB server.

Android 16 reported ARM64 through ADB and registered SurfaceFlinger. It did not start the package manager. The guest crash buffer recorded repeated `zygote64` failures in ART boot-image initialization, including a segmentation fault in `ImageHeader::GetImageRoot`. This failure occurred before any WoW APK installation.

Android 13 reported `arm64-v8a`, started SurfaceFlinger and `system_server`, scanned 206 system apps, and registered the package manager. `pm list packages -3` returned exit code 0. SurfaceFlinger reported ANGLE OpenGL ES 3.1 backed by Mesa Lavapipe.

The signed APK and game archive matched their local SHA-256 hashes after copying. The archive was checked for credential handoff files, unsafe paths and links before extraction.

Android 13 lost the package service while `adb install` was running. The install returned `Can't find service: package`. The crash buffer showed repeated ART assertions in `system_server` while initializing the virtual fingerprint service. ART interpreter mode restarted `zygote64` without bringing up the package service. Temporarily removing the optional fingerprint feature allowed the package service to return, but ART then aborted in `NetworkStatsObs`. After a full guest restart with QEMU's `max` CPU and single-threaded TCG, `system_server` again crashed before shared storage mounted, this time with `SIGSEGV` in `NativeTombstone`.

The APK never installed successfully on these Google SDK guests. The emulators and test ADB listeners were closed.

## Additional ARM64 guest tests on the VPS

The official Android 11 AOSP ARM64 image reproduced startup segmentation faults in `libcodec2_vndk.so` with Android Emulator 37.2.13 and 35.3.12. `app_process64` and several media processes crashed before the package service became ready. Neither test reached APK installation.

Cuttlefish release build `16644040` was fetched with its ARM64 system image and matching x86-64 host tools. QEMU accepted the private runtime setup, but the launcher rejected user-space vsock with its QEMU backend. The alternative stopped during assembly because the host lacks the `cvdnetwork` group. No host group or network configuration was changed.

## Redroid ARM64 virtual guest

The Debian 13 ARM64 guest used upstream QEMU software CPU emulation and an Android 13 Redroid container with Binder devices inside the guest. Android completed boot and reported `arm64-v8a`, ANGLE EGL and the `pastel` Vulkan driver. The signed emulator-only APK installed successfully and reported WoW client `1.60.1.70888` ready after importing the approved credential-free seed. Storage access was granted to the app inside the guest.

The manual launch at 720p finished installing verified Proton 11 ARM64EC, created the Wine prefix and began extracting the Bionic runtime. The app stayed running during this stage. No Battle.net credentials were entered.

The original Bionic archive matched the published download. The uncompressed tar SHA-256 was `782cbcbf3eb2ad3c4ad446a98e1db81502613aae06cd8f658178c9a35829b6d5` before and after repacking to reduce decompression time. Native unpacking inside the guest staged that same runtime with the APK's redirect libraries and the published extras. The app accepted runtime version 31. The original partial app extraction was retained.

Android killed the test app after a five-second focus-event ANR during game-view startup. The main thread was waiting in Android's hardware renderer, and `RenderThread` was blocked in an ART JNI call during a frame-commit callback. The Skia Vulkan UI retry advanced to the full-screen X server and loaded the controller profile, then hit another Android ANR during setup.

Redroid's system Vulkan probe reported SwiftShader 1.2 with neither `VK_EXT_robustness2` nor `VK_EXT_transform_feedback`. A separate ARM64 Bionic probe loaded signature-verified Termux Mesa 26.2.6 and reported llvmpipe Vulkan 1.4.354, including both extensions, swapchain and X11 surface support. This probe did not exercise Wine or WoW.

After restarting Redroid with Android's software-emulator timeout multiplier set to 12 and a 540 × 960 virtual display, Wine 11 launched the ARM64 WoW client. The client rendered its first-run data setup and finished it. Its `gx.log` then reported `LoadLibrary(dxgi.dll) Failed (HR:0x0000007E)` followed by `No suitable gpu found`. The stock Proton ARM64 `dxgi.dll`, `d3d11.dll` and `wined3d.dll` were present in the shared lower prefix and absent from the active upper prefix.

Placing verified copies of those three stock Wine DLLs in the upper prefix did not change the `dxgi.dll` load error. The private Mesa probe initially conflicted with the app runtime's older C++ library. Isolating the probe's C++ dependency let the app's bundled Vulkan loader 1.4.315 enumerate llvmpipe Vulkan 1.4.354. The next guest launch used that driver and the signed APK's two bundled ARM64 DXVK DLLs. The prior Wine DLLs and Vulkan manifest were saved in the guest.

The game process stalled before writing a new graphics log with that configuration. A separate Wine `rundll32` probe using the same prefix confirmed that the ARM64 DXVK `dxgi.dll` loaded as a native module and ran its process-attach handler. The probe intentionally requested a nonexistent entry point. It did not create a graphics device or test a game frame. The next run disabled DRI3 for the test container and enabled scoped Wine graphics logs.

With native ARM64 DXVK and the original system Vulkan driver, WoW reached `D3D11CreateDevice`. Its graphics log identified `Wrapper(SwiftShader Device (LLVM 10.0.0))`, reported `0x80070057`, and exited because the adapter could not provide Direct3D feature level 11_0. Using the original driver for the Wine desktop and selecting Mesa only for a separate ARM64 Direct3D probe succeeded. DXVK enumerated llvmpipe 26.2.6, reported a maximum feature level of 11_1, and returned `0x00000000` for `D3D11CreateDevice` at feature level 11_0.

A test-only native helper kept the original driver through Wine and client startup, then selected Mesa only in the real WoW process when `gx.log` opened. The client selected llvmpipe, reported `Dx11 Device Create Successful`, and used shader model 5. The Wine trace also recorded swapchain creation. The game process then ended and Android closed the X server. Socket write errors occurred while disconnected X clients were being cleaned up. The container recorded zero kernel OOM kills.

## Confirmed Battle.net login on the Redroid ARM64 virtual guest (2026-11-11)

**PASS — the ARM64 game rendered the Battle.net login form without a physical device.** The same signed emulator debug APK (SHA-256 `0572cc7a3e0135e99d6115176fee6034795f71482c1a09a95a8edeefa361fcd8`) launched client `1.60.1.70888` through the app's **Log in manually** button. The game rendered the intro cinematic. One Escape key event advanced to the login form with visible Email or Phone, Password, Remember Account and Log In controls. Both credential fields were empty. The test stopped at the form.

The working guest profile used the APK's bundled ARM64 DXVK DLLs, Mesa Lavapipe selected only in the WoW process, and `MESA_VK_WSI_DEBUG=sw,noshm` for X11 transport. Android ran with the software-emulator timeout multiplier of 12, a 5 GiB container memory limit and four virtual CPUs. The game logged Direct3D 11 device creation at 22:47:37 UTC and recreated the device at 23:11:16 UTC after a graphics fence timeout. The test used a prepared Wine prefix and credential-free game seed on the private VPS. The signed APK was unchanged during the guest tuning. A stock emulator and the protected Galaxy S25+ APK were outside this successful run.

The local evidence is `app/build/wow-lab-artifacts/wow-redroid-battlenet-login.png` (SHA-256 `6b0da16488d10f6894bcffe8c00c17d75ac71b4dea871edbf98d3e745c0f2f83`). The private VPS lab retains the screenshot, graphics log and working guest disk.

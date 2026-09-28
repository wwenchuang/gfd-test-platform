# Sonic Android v2.0.8 touch queue patch candidate

This patch addresses a demonstrable source race in SonicCloudOrg/sonic-android-apk v2.0.8 `SonicPluginTouchService.kt`. It is a source candidate, not a built APK, deployed fix, or device acceptance result.

## Defect and fix

The socket reader writes `events[0].action/x/y`, then posts a lambda that reads the same mutable Event later. If the Handler is delayed until after both `down` and `up` are read, both queued callbacks observe `up`. A queued drag can likewise lose intermediate coordinates. This interleaving does not require concurrent execution of two callbacks; an ordinary delayed Looper is sufficient.

The patch captures each parsed command's action and optional coordinates in local values. Every shared Event update, including `down` timestamp generation inside `getMotionEvent`, runs on the same Handler in command order. An `up` retains the preceding coordinates and gesture downTime; the next `down` establishes a new downTime. Unsupported commands and release retain the original behavior.

Constructing MotionEvent before posting would also require moving or redesigning timestamp and pointer state ownership: `getMotionEvent` mutates Event.down and shared PointerCoords. A simple Event copy can give `up` the old downTime if the preceding queued down has not executed. Handler ownership keeps these existing gesture semantics together. Each obtained MotionEvent is recycled in `finally` after the injection call returns, including when injection throws. The matching v2.0.8 InputManagerWrapper was inspected: it invokes the Android injection method synchronously through reflection and does not retain the MotionEvent in wrapper state. Android runtime behavior still needs device validation; the wrapper also ignores the reflected method's Boolean result, so its return alone is not delivery evidence.

## Evidence and limits

A temporary Python scheduler model deliberately holds all callbacks until the socket reader has consumed the commands. It reproduces lost actions in the original ownership pattern and passes tap, drag, and two-tap action/coordinate/downTime assertions with the patched pattern. Source assertions also verify that processLoop does not touch the shared Event before posting and that the Handler recycles the MotionEvent. This model validates the scheduling argument; it is not a Kotlin unit test or Android runtime test.

`git apply --check` and an actual apply against a temporary tree containing the downloaded v2.0.8 source both pass. The patched file matches the generated candidate byte for byte. No JDK/Android SDK build was performed, no APK was produced, and no device was operated for this patch.

Parent-session observations: default-mode tap on My stayed on the PHM110 Android 15 homepage for more than a minute; official repair touch switched to ADB and the same navigation worked, as did Printing Records after settling. There was no active platform recording in that diagnostic. These observations isolate a difference between touch paths but do not prove this race caused that device's failure. AndroidWSServer v2.7.2 handles browser `touch` messages by calling `writeToOutputStream` directly; this path does not use the 300 ms pause in the separate AndroidTouchHandler.tap helper. Browser down/up timing can therefore expose the queue interleaving described above.

Other candidates include Android 15 hidden-API/injection behavior, wrapper permissions or injection failure, a missing/dead touch socket/service, and coordinate/rotation mapping. The matching installed versions and injection results must be checked before attributing the device failure to this patch. ADB mode success is evidence of a working alternative path, not APK repair acceptance. Do not automatically repeat a possibly delivered action through ADB; that can perform a physical action twice.

## Apply and build

Use a clean checkout of SonicCloudOrg/sonic-android-apk at `v2.0.8` and preserve its licensing and signing requirements:

```bash
git apply --check /absolute/path/sonic-android-touch-v2.0.8.patch
git apply /absolute/path/sonic-android-touch-v2.0.8.patch
git diff --check
./gradlew :app:assembleDebug
```

The Gradle wrapper requires a compatible JDK and the Android SDK/build tools declared by that upstream checkout. Check its Gradle and Android plugin versions to choose the matching JDK; no local toolchain compatibility is asserted here. A production replacement also requires the existing package identity/signing policy and Sonic Agent's APK provisioning path to be verified. Deploying platform Python/JS alone does not install this APK source patch.

After a successful build, test injection success/error reporting, then validate default Sonic APK touch on the explicitly authorized phone **PHM110 / `ecbfd645`**. `9888E0094F2A` is a printer identifier, not the phone. Do not select another phone if PHM110 is unavailable. Verify ordinary taps, rapid down/up, drag coordinates, consecutive gestures, and recovery after reconnect; separately run the platform's recording and real Runner replay acceptance. Keep default touch unverified until those checks pass.

package app.wowforever.securityprobe;

import android.app.Activity;
import android.app.Instrumentation;
import android.os.Bundle;
import android.os.Environment;
import android.os.Process;

import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;

/** Runs in the probe's ordinary app UID, with no root, network or launcher instrumentation access. */
public final class StorageProbe extends Instrumentation {
    private Bundle arguments;

    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        arguments = args;
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            if (Process.myUid() != getTargetContext().getApplicationInfo().uid || !Environment.isExternalStorageManager()) {
                throw new IOException("Grant all-files access to the ordinary probe app before running");
            }
            try (FileInputStream shared = new FileInputStream(arguments.getString("shared"))) {
                if (shared.read() != 'S') throw new IOException("Shared canary missing or invalid");
            }
            try (FileInputStream privateCanary = new FileInputStream(arguments.getString("private"))) {
                privateCanary.read();
                throw new IOException("Probe could open the launcher private canary");
            } catch (FileNotFoundException | SecurityException denied) {
                // The runner confirms independently that this exact private canary exists.
            }
            result.putString("stream", "\nPASS: shared canary readable; launcher private canary inaccessible.\n");
            finish(Activity.RESULT_OK, result);
        } catch (Exception failure) {
            result.putString("stream", "\nFAIL: " + failure.getMessage() + "\n");
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}

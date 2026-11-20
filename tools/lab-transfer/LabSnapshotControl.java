/** Disposable-lab system API control; never part of the handheld APK. */
public final class LabSnapshotControl {
    public static void main(String[] args) {
        if (args.length != 1 || !("disable".equals(args[0]) || "enable".equals(args[0]))) {
            System.err.println("usage: LabSnapshotControl disable|enable");
            System.exit(64);
        }
        final boolean enabled = "enable".equals(args[0]);
        try {
            Class<?> binderType = Class.forName("android.os.IBinder");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class).invoke(null, "window");
            if (binder == null) throw new IllegalStateException("Window service is unavailable");
            Object service = Class.forName("android.view.IWindowManager$Stub")
                    .getMethod("asInterface", binderType).invoke(null, binder);
            if (service == null) throw new IllegalStateException("Window service proxy is unavailable");
            Class.forName("android.view.IWindowManager")
                    .getMethod("setTaskSnapshotEnabled", boolean.class).invoke(service, enabled);
            System.out.println("LAB_SNAPSHOT_API_SET enabled=" + enabled + " proxy=" + service.getClass().getName());
            // The caller must verify dumpsys mSnapshotEnabled in the same server PID.
        } catch (Throwable failure) {
            failure.printStackTrace(System.err);
            System.exit(1);
        }
    }
}

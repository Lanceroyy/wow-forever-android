plugins { alias(libs.plugins.android.application) }

android {
    namespace = "app.wowforever.securityprobe"
    compileSdk = 36
    defaultConfig {
        applicationId = "app.wowforever.securityprobe"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "1"
    }
}

pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "wow-forever-android"
include(":app")

// Optional, local test app with a UID separate from the launcher.
if (providers.gradleProperty("securityProbe").orNull == "true") {
    include(":security-probe")
    project(":security-probe").projectDir = file("tools/security-probe")
}

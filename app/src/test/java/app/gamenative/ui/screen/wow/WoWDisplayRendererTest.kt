package app.gamenative.ui.screen.wow

import org.junit.Assert.assertEquals
import org.junit.Test

class WoWDisplayRendererTest {
    @Test
    fun samsungSnapdragon8EliteUsesOpenGl() {
        assertEquals(WoWDisplayRenderer.OPENGL, WoWDisplayRenderer.defaultFor("Samsung", "SM-S938U", "SM8750-AC"))
        assertEquals(WoWDisplayRenderer.OPENGL, WoWDisplayRenderer.defaultFor("samsung", "SM-S936B", ""))
    }

    @Test
    fun otherDevicesKeepVulkan() {
        assertEquals(WoWDisplayRenderer.VULKAN, WoWDisplayRenderer.defaultFor("Samsung", "SM-S918B", "SM8550"))
        assertEquals(WoWDisplayRenderer.VULKAN, WoWDisplayRenderer.defaultFor("AYN", "Odin 3", "SM8750"))
    }
}

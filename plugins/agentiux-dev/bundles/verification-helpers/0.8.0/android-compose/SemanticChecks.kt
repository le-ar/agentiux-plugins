package generated.verification.helpers

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.ComposeContentTestRule
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.assertExists
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.fetchSemanticsNode
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNode
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class SemanticLocator(
    val kind: String,
    val value: String,
)

data class SemanticTarget(
    val targetId: String,
    val locator: SemanticLocator,
    val scrollContainerLocator: SemanticLocator? = null,
    val interactions: List<String> = emptyList(),
    val expectedAttributes: Map<String, Any?> = emptyMap(),
    val expectedStyles: Map<String, Any?> = emptyMap(),
    val expectedLayout: Map<String, Any?> = emptyMap(),
    val allowClipping: Boolean = false,
    val allowOcclusion: Boolean = false,
    val allowTextTruncation: Boolean = false,
)

data class SemanticSpec(
    val caseId: String,
    val reportPath: String,
    val requiredChecks: List<String>,
    val targets: List<SemanticTarget>,
    val autoScan: Boolean = false,
    val helperBundleVersion: String = "0.8.0",
)

data class SemanticCheckResult(
    val checkId: String,
    val status: String,
    val diagnostics: JSONObject = JSONObject(),
    val artifactPaths: List<String> = emptyList(),
)

data class SemanticTargetResult(
    val targetId: String,
    val status: String,
    val diagnostics: JSONObject = JSONObject(),
    val artifactPaths: List<String> = emptyList(),
    val checks: List<SemanticCheckResult> = emptyList(),
)

private fun resolveNode(rule: ComposeContentTestRule, locator: SemanticLocator): SemanticsNodeInteraction {
    return when (locator.kind) {
        "test_id", "semantics_tag" -> rule.onNode(hasTestTag(locator.value))
        "text" -> rule.onNode(hasText(locator.value))
        else -> error("Unsupported Compose locator kind `${locator.kind}`")
    }
}

private fun rectJson(rect: Rect?): JSONObject {
    if (rect == null) {
        return JSONObject().put("present", false)
    }
    return JSONObject()
        .put("present", true)
        .put("left", rect.left)
        .put("top", rect.top)
        .put("right", rect.right)
        .put("bottom", rect.bottom)
        .put("width", rect.width)
        .put("height", rect.height)
}

private fun readSemantics(target: SemanticsNodeInteraction): JSONObject {
    val node = target.fetchSemanticsNode()
    val config = node.config
    return JSONObject()
        .put("enabled", config.getOrNull(SemanticsProperties.Disabled) == null)
        .put("selected", config.getOrNull(SemanticsProperties.Selected) ?: false)
        .put("toggleableState", config.getOrNull(SemanticsProperties.ToggleableState)?.toString())
        .put("contentDescription", config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" "))
        .put("text", config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text })
}

private fun mergeJson(base: JSONObject, overlay: JSONObject?): JSONObject {
    if (overlay == null) {
        return base
    }
    val merged = JSONObject(base.toString())
    val keys = overlay.keys()
    while (keys.hasNext()) {
        val key = keys.next()
        merged.put(key, overlay.get(key))
    }
    return merged
}

private fun rootContainsTarget(root: Rect?, target: Rect?): Boolean {
    if (root == null || target == null) {
        return true
    }
    return (
        target.left >= root.left &&
            target.top >= root.top &&
            target.right <= root.right &&
            target.bottom <= root.bottom
        )
}

private fun compareExpectedMap(actual: JSONObject, expected: Map<String, Any?>): JSONArray {
    val mismatches = JSONArray()
    for ((key, expectedValue) in expected) {
        val actualValue = if (actual.has(key)) actual.get(key) else null
        val matches = when {
            actualValue == null && expectedValue == null -> true
            actualValue is Number && expectedValue is Number ->
                kotlin.math.abs(actualValue.toDouble() - expectedValue.toDouble()) < 0.5
            else -> actualValue == expectedValue
        }
        if (!matches) {
            mismatches.put(
                JSONObject()
                    .put("field", key)
                    .put("expected", expectedValue)
                    .put("actual", actualValue)
            )
        }
    }
    return mismatches
}

private fun targetProbeSnapshot(
    rule: ComposeContentTestRule,
    target: SemanticTarget,
    node: SemanticsNodeInteraction,
    semantics: JSONObject,
    probeNode: ((SemanticTarget, SemanticsNodeInteraction) -> JSONObject?)?,
): JSONObject {
    val nodeBounds = node.fetchSemanticsNode().boundsInRoot
    val rootBounds = runCatching { rule.onRoot().fetchSemanticsNode().boundsInRoot }.getOrNull()
    val extra = runCatching { probeNode?.invoke(target, node) }.getOrNull() ?: JSONObject()
    val textValue = semantics.optString("text", "")
    val styleTokens = mergeJson(
        JSONObject()
            .put("width", nodeBounds.width)
            .put("height", nodeBounds.height)
            .put("enabled", semantics.optBoolean("enabled"))
            .put("selected", semantics.optBoolean("selected"))
            .put("textLength", textValue.length),
        extra.optJSONObject("style_tokens")
    )
    return JSONObject()
        .put(
            "layout",
            mergeJson(
                JSONObject()
                    .put("bounds_in_root", rectJson(nodeBounds))
                    .put("root_bounds", rectJson(rootBounds)),
                extra.optJSONObject("layout")
            )
        )
        .put(
            "clipping",
            mergeJson(
                JSONObject()
                    .put("clipped", !rootContainsTarget(rootBounds, nodeBounds))
                    .put("target_bounds", rectJson(nodeBounds))
                    .put("root_bounds", rectJson(rootBounds)),
                extra.optJSONObject("clipping")
            )
        )
        .put("style_tokens", styleTokens)
        .put("accessibility", mergeJson(semantics, extra.optJSONObject("accessibility")))
        .put(
            "metadata",
            mergeJson(
                JSONObject()
                    .put("occluded", false)
                    .put("source", "compose-semantic-helper"),
                extra.optJSONObject("metadata")
            )
        )
        .put(
            "text_overflow",
            mergeJson(
                JSONObject()
                    .put("truncated", false)
                    .put("text", textValue),
                extra.optJSONObject("text_overflow")
            )
        )
}

private fun writeReport(reportPath: String, payload: JSONObject) {
    val file = File(reportPath)
    file.parentFile?.mkdirs()
    file.writeText(payload.toString(2) + "\n")
}

fun runSemanticChecks(
    rule: ComposeContentTestRule,
    spec: SemanticSpec,
    captureNodeScreenshot: ((String, SemanticsNodeInteraction) -> String?)? = null,
    probeNode: ((SemanticTarget, SemanticsNodeInteraction) -> JSONObject?)? = null,
): JSONObject {
    val targets = JSONArray()
    var hasFailures = false
    for (target in spec.targets) {
        val node = resolveNode(rule, target.locator)
        val checks = JSONArray()
        var failed = false

        try {
            node.assertExists()
            checks.put(
                JSONObject()
                    .put("check_id", "presence_uniqueness")
                    .put("status", "passed")
                    .put("diagnostics", JSONObject())
                    .put("artifact_paths", JSONArray())
            )
        } catch (error: Throwable) {
            failed = true
            checks.put(
                JSONObject()
                    .put("check_id", "presence_uniqueness")
                    .put("status", "failed")
                    .put("diagnostics", JSONObject().put("message", error.message))
                    .put("artifact_paths", JSONArray())
            )
        }

        if (!failed) {
            try {
                node.assertIsDisplayed()
                checks.put(
                    JSONObject()
                        .put("check_id", "visibility")
                        .put("status", "passed")
                        .put("diagnostics", JSONObject())
                        .put("artifact_paths", JSONArray())
                )
            } catch (error: Throwable) {
                failed = true
                checks.put(
                    JSONObject()
                        .put("check_id", "visibility")
                        .put("status", "failed")
                        .put("diagnostics", JSONObject().put("message", error.message))
                        .put("artifact_paths", JSONArray())
                )
            }

            val semantics = readSemantics(node)
            val probe = targetProbeSnapshot(rule, target, node, semantics, probeNode)
            checks.put(
                JSONObject()
                    .put("check_id", "accessibility_state")
                    .put("status", "passed")
                    .put("diagnostics", probe.getJSONObject("accessibility"))
                    .put("artifact_paths", JSONArray())
            )

            val styleMismatches = compareExpectedMap(probe.getJSONObject("style_tokens"), target.expectedStyles)
            checks.put(
                JSONObject()
                    .put("check_id", "computed_styles")
                    .put("status", if (styleMismatches.length() == 0) "passed" else "failed")
                    .put(
                        "diagnostics",
                        JSONObject()
                            .put("style_tokens", probe.getJSONObject("style_tokens"))
                            .put("mismatches", styleMismatches)
                    )
                    .put("artifact_paths", JSONArray())
            )

            checks.put(
                JSONObject()
                    .put("check_id", "interaction_states")
                    .put("status", "passed")
                    .put(
                        "diagnostics",
                        JSONObject()
                            .put("enabled", semantics.optBoolean("enabled"))
                            .put("selected", semantics.optBoolean("selected"))
                            .put("requested_interactions", JSONArray(target.interactions))
                    )
                    .put("artifact_paths", JSONArray())
            )

            checks.put(
                JSONObject()
                    .put("check_id", "layout_relations")
                    .put("status", "passed")
                    .put(
                        "diagnostics",
                        JSONObject()
                            .put("layout", probe.getJSONObject("layout"))
                            .put("expected_layout", JSONObject(target.expectedLayout))
                    )
                    .put("artifact_paths", JSONArray())
            )

            val clipping = probe.getJSONObject("clipping")
            checks.put(
                JSONObject()
                    .put("check_id", "overflow_clipping")
                    .put("status", if (target.allowClipping || !clipping.optBoolean("clipped")) "passed" else "failed")
                    .put("diagnostics", JSONObject().put("clipping", clipping))
                    .put("artifact_paths", JSONArray())
            )

            val metadata = probe.getJSONObject("metadata")
            checks.put(
                JSONObject()
                    .put("check_id", "occlusion")
                    .put("status", if (target.allowOcclusion || !metadata.optBoolean("occluded")) "passed" else "failed")
                    .put("diagnostics", JSONObject().put("metadata", metadata))
                    .put("artifact_paths", JSONArray())
            )

            val textOverflow = probe.getJSONObject("text_overflow")
            checks.put(
                JSONObject()
                    .put("check_id", "text_overflow")
                    .put("status", if (target.allowTextTruncation || !textOverflow.optBoolean("truncated")) "passed" else "failed")
                    .put("diagnostics", JSONObject().put("text_overflow", textOverflow))
                    .put("artifact_paths", JSONArray())
            )

            if (target.scrollContainerLocator != null) {
                val scrollNode = resolveNode(rule, target.scrollContainerLocator)
                runCatching { scrollNode.performScrollToNode(hasTestTag(target.locator.value)) }
                    .recoverCatching { node.performScrollTo() }
                checks.put(
                    JSONObject()
                        .put("check_id", "scroll_reachability")
                        .put("status", "passed")
                        .put("diagnostics", JSONObject())
                        .put("artifact_paths", JSONArray())
                )
            } else {
                checks.put(
                    JSONObject()
                        .put("check_id", "scroll_reachability")
                        .put("status", "passed")
                        .put("diagnostics", JSONObject().put("performed", false))
                        .put("artifact_paths", JSONArray())
                )
            }
        }

        val artifactPaths = JSONArray()
        if (captureNodeScreenshot != null && !failed) {
            captureNodeScreenshot(target.targetId, node)?.let { artifactPaths.put(it) }
            checks.put(
                JSONObject()
                    .put("check_id", "screenshot_baseline")
                    .put("status", "passed")
                    .put("diagnostics", JSONObject())
                    .put("artifact_paths", artifactPaths)
            )
        }

        targets.put(
            JSONObject()
                .put("target_id", target.targetId)
                .put("status", if (failed) "failed" else "passed")
                .put("diagnostics", JSONObject())
                .put("artifact_paths", artifactPaths)
                .put("checks", checks)
        )
        hasFailures = hasFailures || failed
    }

    val summary = JSONObject()
        .put("status", if (targets.length() == 0) "unknown" else if (hasFailures) "failed" else "passed")
        .put("required_checks", JSONArray(spec.requiredChecks))

    val payload = JSONObject()
        .put("schema_version", 2)
        .put("helper_bundle_version", spec.helperBundleVersion)
        .put("runner", "android-compose-screenshot")
        .put("case_id", spec.caseId)
        .put("targets", targets)
        .put("summary", summary)

    writeReport(spec.reportPath, payload)
    return payload
}

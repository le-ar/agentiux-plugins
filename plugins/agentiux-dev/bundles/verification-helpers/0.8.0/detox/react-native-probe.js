export function normalizeReactNativeProbeSnapshot(snapshot = {}) {
  return {
    layout: snapshot.layout ?? {},
    clipping: snapshot.clipping ?? {},
    style_tokens: snapshot.style_tokens ?? {},
    accessibility: snapshot.accessibility ?? {},
    metadata: snapshot.metadata ?? {},
    text_overflow: snapshot.text_overflow ?? {},
  };
}

export function createReactNativeProbe(implementation) {
  return async function runReactNativeProbe(targetId, context = {}) {
    if (typeof implementation !== "function") {
      return normalizeReactNativeProbeSnapshot();
    }
    return normalizeReactNativeProbeSnapshot(await implementation(targetId, context));
  };
}

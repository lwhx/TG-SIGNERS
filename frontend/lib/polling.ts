/** Poll visible pages serially. Cancel stale fetches on unmount or filter changes. */
export function pollVisible(
  load: (signal: AbortSignal) => Promise<void>,
  interval: number,
) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running = false;
  const visible = () => document.visibilityState !== "hidden";
  const run = async () => {
    if (controller.signal.aborted || running || !visible()) return;
    clearTimeout(timer);
    running = true;
    try {
      await load(controller.signal);
    } finally {
      running = false;
      if (!controller.signal.aborted && visible())
        timer = setTimeout(() => void run(), interval);
    }
  };
  const visibility = () => {
    clearTimeout(timer);
    if (visible()) void run();
  };
  document.addEventListener("visibilitychange", visibility);
  void run();
  return () => {
    controller.abort();
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visibility);
  };
}

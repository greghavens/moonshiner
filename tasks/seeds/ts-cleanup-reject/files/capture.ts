// Runs one diagnostics pass against a bench device. The hub hands out
// exclusive device handles; a handle that never goes back starves every
// other bench station, so disposal has to happen on every path.

export interface Frame {
  seq: number;
  payload: string;
}

export interface CaptureStream {
  read(): Promise<Frame[]>;
  stop(): Promise<void>;
}

export interface DeviceHandle {
  startCapture(): Promise<CaptureStream>;
  close(): Promise<void>;
}

export interface DeviceHub {
  open(deviceId: string): Promise<DeviceHandle>;
}

export type CleanupStage = 'capture' | 'device';
export type CleanupHook = (error: unknown, stage: CleanupStage) => void;

export async function withCapture<R>(
  hub: DeviceHub,
  deviceId: string,
  work: (capture: CaptureStream) => Promise<R> | R,
  onCleanupError?: CleanupHook,
): Promise<R> {
  const handle = await hub.open(deviceId);
  const capture = await handle.startCapture();
  try {
    return await work(capture);
  } finally {
    await capture.stop();
    try {
      await handle.close();
    } catch (err) {
      onCleanupError?.(err, 'device');
    }
  }
}

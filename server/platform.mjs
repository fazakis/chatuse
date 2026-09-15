import { join } from 'node:path';

export function nativeCommand(root, platform = process.platform) {
  if (platform === 'darwin') return join(root, 'runtime/Chatuse.app/Contents/MacOS/Chatuse');
  if (platform === 'linux') return join(root, 'runtime/chatuse-native');
  throw new Error('Chatuse supports macOS and Linux/X11. This platform has no native backend.');
}

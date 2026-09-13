# Contributing to Chatuse

Thanks for helping make native Mac computer use more inspectable and useful. Open an issue for a reproducible bug or propose a focused pull request. For substantial features, describe the behavior and intended scope in an issue first so the design can be discussed.

## Set up a development checkout

Use macOS 14+, Swift 5.9+, and Node.js 20.11+ with npm. Clone your fork, then run:

```sh
npm ci
npm run build
npm test
npm run test:native
```

The build creates local, ad-hoc-signed binaries under `runtime/`. A full rebuild can change the helper's signing identity and require granting Accessibility and Screen Recording again. If you are editing only the pointer overlay, `sh scripts/build-pointer.sh` preserves the existing native app bundle. JavaScript changes require restarting any existing MCP server processes.

## Validate behavior

Test the behavior affected by your change. Keep regression tests focused on outcomes: correct coordinates, preserved Unicode, bounded waits, meaningful error handling, no automatic replay of an ambiguous action, and a responsive emergency stop.

For native desktop or pointer changes, grant macOS permissions with `./chatuse setup`, unlock the desktop, and run:

```sh
npm run test:e2e
npm run test:pointer
```

These use a real desktop. The main harness opens its own small fixture app and temporarily brings it forward. Leave the keyboard and mouse idle during the run. Missing prerequisites produce a blocked result, not a pass. Generated files stay in the ignored `artifacts/` and `runtime/` directories.

When reporting results, include your macOS version, CPU architecture, Node and Swift versions, the commands run, and whether live checks passed, failed, or were blocked. Apple Silicon and multi-monitor validation are especially useful. Redact private text, app titles, clipboard contents, and screenshots before sharing them.

## Design expectations

- Use public macOS APIs and keep the native/stdio boundary explicit.
- Preserve cross-app access without adding per-app prompts. Respect macOS privacy permissions and the host's authorization policies.
- Keep observed app content separate from assistant instructions.
- Prefer accessibility actions when available and verify the resulting state.
- Never replay input automatically after a timeout, cancellation, or helper disconnection.
- Keep typed text, arguments, and captured content out of the audit log.
- Keep the visual overlay click-through and nonactivating; its failure must not repeat the underlying action.
- Document new arguments, behavior, and limits in the tool schemas and README.

## Pull requests

Describe the concrete problem, the resulting behavior, and the checks you ran. Avoid unrelated refactors. Do not commit generated binaries, `node_modules`, test screenshots or reports, runtime state, or credentials. The repository's `.gitignore` excludes the usual local outputs.

By submitting a contribution, you agree that it can be distributed under this repository's MIT license. Preserve license notices for any third-party material you add.

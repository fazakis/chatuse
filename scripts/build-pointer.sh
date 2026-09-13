#!/bin/sh
set -eu
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$chatuse_root"
swift build -c release --product chatuse-pointer
chatuse_bin=$(swift build -c release --show-bin-path)
mkdir -p "$chatuse_root/runtime"
cp "$chatuse_bin/chatuse-pointer" "$chatuse_root/runtime/chatuse-pointer.new"
codesign --force --sign - --identifier local.chatuse.pointer "$chatuse_root/runtime/chatuse-pointer.new"
mv "$chatuse_root/runtime/chatuse-pointer.new" "$chatuse_root/runtime/chatuse-pointer"
printf 'Built visual pointer. The authorized Chatuse.app bundle was not modified.\n'

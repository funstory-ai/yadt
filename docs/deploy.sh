#!/bin/bash
set -e

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

echo "check uv installed ……"
if command_exists uv; then
  echo "uv installed !"
  exit 0
fi

echo "uv not install, start installing ……"

OS=$(uname -s)
case "$OS" in
  Linux)
    if command_exists curl; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command_exists wget; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
      echo "curl or wget not found. uv installed failed."
      exit 1
    fi
    ;;
  Darwin)
    if command_exists brew; then
      brew install uv
    else
      echo "Homebrew not installed, please installed uv munally. "
      exit 1
    fi
    ;;
  *)
    echo "not support OS: $OS"
    exit 1
    ;;
esac

if command_exists uv; then
     uv run yadt
     pre-commit install
else
  exit 1
fi

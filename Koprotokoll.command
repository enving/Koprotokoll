#!/bin/zsh
koprotokoll_root="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$koprotokoll_root" || exit 1
exec "$koprotokoll_root/.venv/bin/python" -m koprotokoll desktop

from __future__ import annotations

import argparse
import os
import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--ui', choices=('next', 'legacy'), default=os.environ.get('OMNICLIP_UI', 'next'))
    args, _unknown = parser.parse_known_args(argv)
    return launch_desktop(args.ui)


def launch_desktop(ui_mode: str = 'next') -> int:
    normalized = str(ui_mode or 'next').strip().lower() or 'next'
    if normalized == 'legacy':
        if getattr(sys, 'frozen', False):
            print('Legacy UI is not available in the packaged build.', file=sys.stderr, flush=True)
            return 2
        from ..ui_legacy_tk.app import main as legacy_main

        return legacy_main()
    try:
        from ..ui_next_qt.app import main as qt_main
    except Exception as exc:
        print(f'Qt UI import failed: {exc}', file=sys.stderr, flush=True)
        traceback.print_exc()
        if getattr(sys, 'frozen', False):
            print('Packaged build has no legacy fallback. Please reinstall or repair the app.', file=sys.stderr, flush=True)
            return 1
        print('Falling back to legacy UI.', file=sys.stderr, flush=True)
        from ..ui_legacy_tk.app import main as legacy_main

        return legacy_main()
    return qt_main()

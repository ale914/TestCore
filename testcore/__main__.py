# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Entry point for running TestCore server."""

import argparse
import asyncio
import logging
import sys
from .server import run_server


def setup_logging(loglevel: str = 'info'):
    """Configure logging."""
    level = getattr(logging, loglevel.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def parse_args():
    """Parse command-line arguments (spec §11)."""
    parser = argparse.ArgumentParser(
        description='TestCore Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m testcore                           # Start on 127.0.0.1:6399
  python -m testcore --port 6400               # Custom port
  python -m testcore --bind 0.0.0.0 --port 6399  # Listen on all interfaces
  python -m testcore --loglevel debug          # Enable debug logging
"""
    )

    parser.add_argument(
        '--bind',
        default='127.0.0.1',
        help='Listen address (default: 127.0.0.1)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=6399,
        help='Listen port - 6399 by spec to avoid Redis conflicts (default: 6399)'
    )

    parser.add_argument(
        '--driver-timeout',
        type=float,
        default=5.0,
        help='Watchdog timeout in seconds for driver calls (default: 5.0)'
    )

    parser.add_argument(
        '--max-clients',
        type=int,
        default=64,
        help='Maximum simultaneous client connections (default: 64)'
    )

    parser.add_argument(
        '--journal-size',
        type=int,
        default=1000,
        help='Journal ring buffer size (default: 1000)'
    )

    parser.add_argument(
        '--parallel',
        action='store_true',
        default=False,
        help='Per-instrument locking: commands on different instruments '
             'run concurrently (default: global serial dispatch)'
    )

    parser.add_argument(
        '--loglevel',
        choices=['debug', 'info', 'warning', 'error'],
        default='info',
        help='Logging verbosity (default: info)'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    setup_logging(args.loglevel)

    logger = logging.getLogger(__name__)
    from . import __version__
    logger.info(f"TestCore Server v{__version__}")
    logger.info(f"Starting server on {args.bind}:{args.port}")

    # Initialize registry with driver timeout before starting server
    from .instruments import get_registry
    get_registry(driver_timeout=args.driver_timeout)
    logger.info(f"Driver timeout: {args.driver_timeout}s")

    # Initialize journal with configured size
    from .journal import get_journal
    get_journal(maxlen=args.journal_size)
    logger.info(f"Journal size: {args.journal_size}")

    # Configure dispatch mode
    if args.parallel:
        from .commands import dispatcher
        dispatcher.set_parallel(True)
        logger.info("Dispatch mode: parallel (per-instrument locking)")

    try:
        asyncio.run(run_server(args.bind, args.port, args.max_clients))
    except KeyboardInterrupt:
        print("\nShutdown complete")


if __name__ == "__main__":
    main()

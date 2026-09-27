"""Compatibility entry point; the research command is now record.py."""
import logging

from record import main


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    main()

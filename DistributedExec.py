import multiprocessing
import sys

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from distributedexec.cli import main
    try:
        raise SystemExit(main())
    except Exception:
        import logging
        logging.exception('DistributedExec startup failed')
        if sys.stderr:
            import traceback
            traceback.print_exc()
        raise SystemExit(1)

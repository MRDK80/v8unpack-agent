"""Package entry-point for `python -m v8unpack_agent.scan_forms`.

Delegating here instead of running scan_forms.py directly prevents the
RuntimeWarning caused by the module being imported twice (once via
__init__.py and once as __main__).
"""
from v8unpack_agent.scan_forms import main

if __name__ == "__main__":  # pragma: no cover
    main()

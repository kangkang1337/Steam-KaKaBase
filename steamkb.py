"""Backward-compatible launcher for Steam-KaKaBase.

The backend implementation now lives in the ``backend`` package. Existing
scripts can continue to launch this file unchanged.
"""

from backend.main import main


if __name__ == "__main__":
    main()

"""Entrypoint for `python -m strata.runner`.

Used by the StrataCI CodeBuild runner (buildspec) and the prebaked runner image
(Dockerfile ENTRYPOINT). Delegates to the runner's main().
"""

from strata.runner.main import main

raise SystemExit(main())

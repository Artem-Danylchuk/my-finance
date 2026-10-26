"""PythonAnywhere entrypoint. Configure TRACKER_* environment values beforehand."""

from tracker import create_app

application = create_app()

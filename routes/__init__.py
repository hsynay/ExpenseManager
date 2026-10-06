"""Route modules. Importing this package registers every route on
the app from core.py. Each module uses @app.route directly, so the
endpoint names stay the plain function names (no Blueprint)."""
from routes import auth  # noqa: F401
from routes import projects  # noqa: F401
from routes import income  # noqa: F401
from routes import expenses  # noqa: F401
from routes import checks  # noqa: F401
from routes import reports  # noqa: F401

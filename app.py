"""Entry point: gunicorn loads app:app from here.

The app object lives in core.py and the routes in the routes
package. Importing routes registers every @app.route on the
app. The other names below are imported by the tests and by
anyone who used to find them in this file."""
from db import get_connection
from core import app
from helpers import claim_submission_token, PAGE_SIZE, safe_next
from reconcile import reconcile_supplier_payments, reconcile_customer_payments
import routes  # noqa: F401  registers the routes


if __name__ == '__main__':
    app.run(debug=True)
